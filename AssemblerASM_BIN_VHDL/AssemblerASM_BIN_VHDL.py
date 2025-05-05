"""
AssemblerASM_BIN_VHDL.py   (maio‑2025 – agora com expansão de macros)
--------------------------------------------------------------------
• Lê  ASM.txt   ➜  gera
      ├─ BIN.txt      (tmp(i) := OPC & "bits";        — 13 bits)
      └─ initROM.mif  (mesmo conteúdo em binário)

• Suporta labels, .equ, 8/9 bits de imediato, comentários, E
  expande macros como SOMAi e INCR9 antes da 2ª passada.
"""

from __future__ import annotations
import re
from pathlib import Path

# ───────────────────  CONFIGURAÇÃO  ───────────────────────────────
inputASM   = Path("ASM.txt")
outputBIN  = Path("BIN.txt")
outputMIF  = Path("initROM.mif")

noveBits   = True                               # 9‑bits (bit de habilita)

OPCODE_WIDTH     = 4
IMMEDIATE_WIDTH  = 9 if noveBits else 8
WORD_WIDTH       = OPCODE_WIDTH + IMMEDIATE_WIDTH

mne: dict[str, int] = {
    "NOP": 0x0,
    "LDA": 0x1,
    "SOMA":0x2,
    "SUB": 0x3,
    "LDI": 0x4,
    "STA": 0x5,
    "JMP": 0x6,
    "JEQ": 0x7,
    "CEQ": 0x8,
    "JSR": 0x9,
    "RET": 0xA,
    "AND": 0xB,
    "ANDI":0xC,   
    "JLT": 0xD,
    "CLT": 0xE,       # já era suportado
}

# ───────────────────  REGEX auxiliares  ───────────────────────────
COMMENT_RE = re.compile(r"#.*$")
LABEL_RE   = re.compile(r"^([A-Za-z_]\w*):")
EQU_RE     = re.compile(r"^([A-Za-z_]\w*)\s*\.equ\s*(\d+)")

# macro‑regex (SOMAi $ k , regX)   — tolera espaços extras
SOMAI_RE   = re.compile(r"^SOMAi\s+\$\s*(\d+)\s*,\s*(\w+)", re.I)
INCR9_RE   = re.compile(r"^INCR9", re.I)

# ───────────────────  FUNÇÕES utilitárias  ────────────────────────
def read_file(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()

def strip_comment(line: str) -> tuple[str, str]:
    m = COMMENT_RE.search(line)
    if m:
        return line[:m.start()].rstrip(), m.group()[1:].strip()
    return line.rstrip(), ""

def is_empty(line: str) -> bool:
    return len(line.strip()) == 0

# ───────────────────  EXPANSÃO DE MACROS  ─────────────────────────
def expand_macros(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if is_empty(line):
            out.append("")
            continue

        l = line.lstrip()                 # <<< NOVO

        # ---------- SOMAi -----------------------------------------
        m = SOMAI_RE.match(l)             # usa l em vez de line
        if m:
            k_val, dst = m.groups()
            out.extend([
                f"LDI ${k_val}",
                f"SOMA @{dst}",
                f"STA @{dst}",
            ])
            continue

        # ---------- INCR9 -----------------------------------------
        if INCR9_RE.match(l):             # usa l
            out.extend([
                "LDI $9",
                "SOMA @ADDR_PTR",
                "STA @ADDR_PTR",
            ])
            continue

        # nenhum macro → copia linha original
        out.append(line)
    return out


# ───────────────────  CLASSE ASSEMBLER  ───────────────────────────
class Assembler:
    def __init__(self, mne_map: dict[str, int], opcode_len: int, imm_len: int):
        self.mne  = mne_map
        self.op_w = opcode_len
        self.imm_w = imm_len

    # ---------- 1ª passada: coleta labels / .equ ------------------
    def _first_pass(self, lines: list[str]):
        pc = 0
        labels, consts, cleaned = {}, {}, []

        for ln, raw in enumerate(lines, 1):
            line, _ = strip_comment(raw)

            # .equ
            if m := EQU_RE.match(line):
                consts[m.group(1)] = int(m.group(2))
                cleaned.append("")
                continue

            # label:
            if m := LABEL_RE.match(line):
                name = m.group(1)
                if name in labels:
                    raise ValueError(f"Label repetido '{name}' (linha {ln})")
                labels[name] = pc
                line = line[m.end():].lstrip()

            if is_empty(line):
                cleaned.append("")
                continue

            cleaned.append(line)
            pc += 1

        return cleaned, consts, labels, pc - 1

    # ---------- converte operando para bits -----------------------
    def _encode_immediate(self, token: str, symbols: dict[str, int]) -> str:
        if token.startswith("$"):
            val = int(token[1:])

        elif token.startswith("@"):
            arg = token[1:]
            val = int(arg) if arg.isdecimal() else symbols.get(arg, None)
            if val is None:
                raise ValueError(f"Símbolo não definido: {arg}")

            if noveBits:
                hab = 1 if val > 0xFF else 0
                val &= 0xFF
                return f"{hab:1b}{val:08b}"

        else:
            if token not in symbols:
                raise ValueError(f"Símbolo não definido: {token}")
            val = symbols[token]

        return f"{val:0{self.imm_w}b}"[-self.imm_w:]

    # ---------- linha →  (constante-opcode, bits) -----------------
    def _line_to_const_bits(self, line: str, symbols: dict[str, int]):
        parts = line.split()
        mnem  = parts[0].upper()

        if mnem not in self.mne:
            raise ValueError(f"Mnemónico desconhecido '{mnem}'")

        const_name = mnem
        imm_bits   = "0"*self.imm_w if len(parts)==1 \
                     else self._encode_immediate(parts[1], symbols)
        return const_name, imm_bits

    # ---------- assemble completo --------------------------------
    def assemble(self, asm_path: Path):
        lines    = read_file(asm_path)
        cleaned, consts, labels, _ = self._first_pass(lines)
        expanded = expand_macros(cleaned)                     # ★ NOVO

        symbols = {**consts, **labels}

        vhdl, mif = [], []
        addr_w = len(str(len(expanded)-1))
        pc = 0

        for raw_line, inst in zip(lines, expanded):
            _, comment = strip_comment(raw_line)

            if is_empty(inst):
                vhdl.append(f"-- {comment}" if comment else "")
                continue

            const, bits = self._line_to_const_bits(inst, symbols)

            vhdl.append(
                f'tmp({pc}) := {const} & "{bits}"; -- {inst}'
                f'{("  # " + comment) if comment else ""}'
            )
            mif.append(
                f'\t{str(pc).ljust(addr_w)} : {const} & "{bits}"; -- {inst}'
            )
            pc += 1

        return vhdl, mif

# ───────────────────  MAIN  ───────────────────────────────────────
def main():
    asm = Assembler(mne, OPCODE_WIDTH, IMMEDIATE_WIDTH)
    vhdl, mif = asm.assemble(inputASM)

    outputBIN.write_text("\n".join(vhdl), encoding="utf-8")
    print(f"Gerado {outputBIN}")

    depth = 2 ** ((len(mif)).bit_length())
    header = [
        f"WIDTH={WORD_WIDTH};",
        f"DEPTH={depth};",
        "",
        "ADDRESS_RADIX=DEC;",
        "DATA_RADIX=BIN;",
        "",
        "CONTENT BEGIN",
        "",
    ]
    footer = ["END;"]

    outputMIF.write_text("\n".join(header + mif + footer), encoding="utf-8")
    print(f"Gerado {outputMIF}")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("[ERRO]", exc)

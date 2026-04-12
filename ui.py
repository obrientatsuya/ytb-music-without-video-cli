"""ui.py - Seleção de resultado no terminal. Retorna índice (0-based)."""

import sys


def pick_result(results: list[dict]) -> int | None:
    print()
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['title']}  ({r['duration']})")
    print()
    while True:
        raw = input(f"Escolha [1-{len(results)}] ou q: ").strip().lower()
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            return int(raw) - 1  # índice 0-based
        print("    Opção inválida.")

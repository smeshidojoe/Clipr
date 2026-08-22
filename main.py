"""Запуск Clipr: python main.py

Вся логика старта — в clipr/bootstrap.py, чтобы `python -m clipr` и собранный
exe шли ровно тем же путём.
"""

from clipr.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())

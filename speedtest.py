#!/usr/bin/env python3
"""Замер скорости скачивания: N последовательных запросов к одному адресу.

Использует только стандартную библиотеку Python 3.9+.
"""

import argparse
import sys
import time
import urllib.error
import urllib.request

CHUNK_SIZE = 64 * 1024
USER_AGENT = "speedtest-script/1.0"


def fetch(url: str, timeout: float) -> tuple[int, float]:
    """Скачивает тело ответа целиком. Возвращает (байты, секунды)."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            # Без сжатия, чтобы считать реальный объём файла.
            "Accept-Encoding": "identity",
        },
    )
    size = 0
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
    elapsed = time.perf_counter() - start
    return size, elapsed


def human_bytes(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.2f} {unit}" if unit != "Б" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.2f} ГБ"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Делает N последовательных запросов к URL и считает скорость скачивания."
    )
    parser.add_argument("url", help="адрес, к которому стучаться (например, тяжёлая картинка)")
    parser.add_argument("-n", "--count", type=int, default=10, help="число запросов (по умолчанию 10)")
    parser.add_argument("-t", "--timeout", type=float, default=60.0, help="таймаут одного запроса, сек (по умолчанию 60)")
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count должен быть >= 1")
    if not args.url.startswith(("http://", "https://")):
        parser.error("URL должен начинаться с http:// или https://")

    print(f"URL: {args.url}")
    print(f"Запросов: {args.count}\n")

    total_bytes = 0
    total_time = 0.0
    ok = 0

    for i in range(1, args.count + 1):
        try:
            size, elapsed = fetch(args.url, args.timeout)
        except urllib.error.HTTPError as e:
            print(f"[{i:>2}/{args.count}] ошибка HTTP {e.code}: {e.reason}")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            print(f"[{i:>2}/{args.count}] ошибка: {reason}")
            continue

        ok += 1
        total_bytes += size
        total_time += elapsed
        speed = size / elapsed / 1024 / 1024 if elapsed > 0 else 0.0
        print(f"[{i:>2}/{args.count}] {human_bytes(size):>10}  {elapsed * 1000:9.1f} мс  {speed:8.2f} МБ/с")

    print()
    if ok == 0:
        print("Ни один запрос не завершился успешно.", file=sys.stderr)
        return 1

    avg_time = total_time / ok
    speed_mbs = total_bytes / total_time / 1024 / 1024 if total_time > 0 else 0.0
    speed_mbits = total_bytes * 8 / total_time / 1_000_000 if total_time > 0 else 0.0

    print(f"Успешных запросов:     {ok}/{args.count}")
    print(f"Среднее время запроса: {avg_time * 1000:.1f} мс")
    print(f"Всего скачано:         {human_bytes(total_bytes)} ({total_bytes} байт)")
    print(f"Скорость скачивания:   {speed_mbs:.2f} МБ/с ({speed_mbits:.2f} Мбит/с)")
    return 0 if ok == args.count else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)

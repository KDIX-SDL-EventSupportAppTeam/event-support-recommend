"""目視確認レポートを HTML ファイルに書き出す。

    python tools/build_report.py            # -> tools/out/index.html
    python tools/build_report.py path.html  # 出力先を指定

engine は合成データのみを使うので DB 接続も .env も不要。
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from event_support_recommend.demo import build_report_html  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report_html(), encoding="utf-8")
    print(f"wrote {out.resolve()}")
    try:
        webbrowser.open(out.resolve().as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()

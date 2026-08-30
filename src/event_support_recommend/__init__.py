"""event-support-recommend — 第4回プロトフェス向け 推薦マイクロサービス。

依存の向きは api/ -> strategies/ -> features/ - drsa/ -> data/ の一方向のみ
(docs/specs/08-architecture.md §1)。features/ と drsa/ は FastAPI/SQL を知らない純関数。
"""

__version__ = "0.1.0"

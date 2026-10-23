import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = ROOT / "schemas"


def main() -> int:
    # Resolve the package from the repo regardless of the current directory.
    sys.path.insert(0, str(ROOT / "src"))
    from auto_zenn_pipeline.models import export_json_schemas

    SCHEMAS_DIR.mkdir(exist_ok=True)
    for name, text in export_json_schemas().items():
        (SCHEMAS_DIR / name).write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

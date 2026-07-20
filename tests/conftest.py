from pathlib import Path

from da_agent.config import load_dotenv

# 加载仓库根 .env（含 LLM key；文件在 gitignore 中）
load_dotenv(Path(__file__).parent.parent / ".env")

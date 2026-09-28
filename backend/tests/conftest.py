import os
import tempfile

# Isolate tests from any real .env: temp DB, local memory, no LLM key, no Hindsight.
_tmp = tempfile.mkdtemp(prefix="memoryops-test-")
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["LOCAL_MEMORY_PATH"] = os.path.join(_tmp, "mem.json")
os.environ["MEMORY_BACKEND"] = "local"
os.environ["GROQ_API_KEY"] = ""
os.environ["HINDSIGHT_BASE_URL"] = ""
os.environ["HINDSIGHT_API_KEY"] = ""

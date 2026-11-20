import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

script = Path(__file__).resolve().parents[1] / 'tools' / 'backup.py'
# O Dockerfile copia também tools em /app/tools.
spec = importlib.util.spec_from_file_location('backup', script)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def test_tampered_backup_never_creates_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.dump'
            path.write_bytes(b'altered')
            path.with_suffix('.json').write_text(json.dumps({'bytes': 1, 'sha256': 'invalid'}))
            with patch.object(backup, 'sql') as sql, self.assertRaises(ValueError):
                backup.verify(path)
            sql.assert_not_called()

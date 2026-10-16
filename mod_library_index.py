"""Disposable inventory cache; source mods remain authoritative and read-only."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile

from game_resources import user_data_directory


def stamp(path):
    try:
        st = path.lstat()
        return [st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode,
                getattr(st, "st_file_attributes", 0)]
    except OSError:
        return None


class LibraryIndex:
    def __init__(self, root):
        self.root = root
        key = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
        self.path = user_data_directory() / "mod-library" / f"{key}.json"
        self.entries, self.used = {}, set()
        try:
            if self.path.stat().st_size <= 8 * 1024 * 1024:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") == 1 and data.get("root") == str(root) and isinstance(data.get("entries"), dict):
                    self.entries = data["entries"]
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def relative(self, path):
        return path.relative_to(self.root).as_posix()

    def source_path(self, value):
        if not isinstance(value, str) or "\\" in value or ":" in value:
            raise ValueError("Invalid cached path")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid cached path")
        return self.root.joinpath(*relative.parts)

    def get(self, key, check=True):
        from mod_library import LibraryMod
        self.used.add(key)
        try:
            entry = self.entries[key]
            watches = entry["watches"]
            if not isinstance(watches, dict) or not watches:
                return None
            if check and any(stamp(self.source_path(p)) != saved for p, saved in watches.items()):
                return None
            records = []
            for row in entry["records"]:
                if (not isinstance(row["name"], str) or not isinstance(row["kind"], str)
                        or not isinstance(row["box_state"], str) or not isinstance(row["relative"], str)
                        or (row["model_count"] is not None and (type(row["model_count"]) is not int or row["model_count"] < 0))):
                    return None
                records.append(LibraryMod(row["name"], self.source_path(row["path"]), row["kind"],
                                          row["model_count"], row["box_state"], row["relative"]))
            errors = entry["errors"]
            if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
                return None
            return records, errors
        except (KeyError, ValueError, TypeError, AttributeError):
            return None

    def remember(self, key, records, errors, watches):
        self.used.add(key)
        # If a directory changed while it was enumerated, do not cache a partial
        # snapshot under its final timestamp. Revisit it on the next refresh.
        if not watches or any(stamp(self.source_path(p)) != saved for p, saved in watches.items()):
            self.entries.pop(key, None)
            return
        self.entries[key] = {
            "watches": watches,
            "records": [{"name": r.name, "path": self.relative(r.path), "kind": r.kind,
                         "model_count": r.model_count, "box_state": r.box_state, "relative": r.relative}
                        for r in records],
            "errors": list(errors),
        }

    def save(self):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "root": str(self.root),
                    "entries": {k: v for k, v in self.entries.items() if k in self.used}}
            # Atomic replacement also tolerates two program windows scanning.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="index-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump(data, output, ensure_ascii=False)
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            pass  # A read-only profile must not prevent opening a mod.
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

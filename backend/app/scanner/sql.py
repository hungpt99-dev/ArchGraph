"""SQL / migration extractor: schema and table-access discovery."""

from __future__ import annotations

import re

from .base import FileResult, RepoContext, SNode, line_of, mask_comments

CREATE_RE = re.compile(
    r"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\((.*?)\)\s*(?:;|$)"
)
ALTER_RE = re.compile(r"(?i)alter\s+table\s+[`\"\[]?([\w.]+)[`\"\]]?")
REF_RE = re.compile(r"(?i)\b(?:from|join|into|update|truncate|delete\s+from)\s+[`\"\[]?([\w.]+)[`\"\]]?")


class SqlExtractor:
    language = "sql"
    extensions = (".sql",)

    def extract(self, path: str, text: str, ctx: RepoContext) -> FileResult:
        res = FileResult(path=path, language="sql")
        code = mask_comments(text, line_comment="--")
        db_key = f"db:{ctx.repository_name}"
        tables: dict[str, str] = {}
        for m in CREATE_RE.finditer(code):
            table = m.group(1).split(".")[-1]
            tables[table] = m.group(2)
            tkey = f"table:{ctx.repository_name}:{table}"
            res.add(SNode(key=tkey, type="TABLE", name=table, qualified_name=table,
                          parent_key=db_key,
                          properties={"source": "sql-migration", "file": path},
                          detection="STATIC_AST", confidence=0.95,
                          file_path=path, start_line=line_of(text, m.start())))
            for line in m.group(2).splitlines():
                col = re.match(r"\s*[`\"\[]?(\w+)[`\"\]]?\s+[A-Za-z]", line)
                if col and col.group(1).upper() not in (
                    "PRIMARY", "FOREIGN", "UNIQUE", "CONSTRAINT", "KEY", "INDEX", "CHECK"
                ):
                    res.add(SNode(key=f"{tkey}:col:{col.group(1)}", type="COLUMN",
                                  name=col.group(1), qualified_name=f"{table}.{col.group(1)}",
                                  parent_key=tkey, properties={}, detection="STATIC_AST",
                                  confidence=0.9))
        for m in ALTER_RE.finditer(code):
            table = m.group(1).split(".")[-1]
            tables.setdefault(table, "")
            tkey = f"table:{ctx.repository_name}:{table}"
            tail = code[m.end(): m.end() + 200]
            for col in re.findall(r"(?i)\badd\s+(?:column\s+)?[`\"\[]?(\w+)[`\"\]]?\s+[a-z]", tail):
                res.add(SNode(key=f"{tkey}:col:{col}", type="COLUMN", name=col,
                              qualified_name=f"{table}.{col}", parent_key=tkey,
                              properties={}, detection="STATIC_AST", confidence=0.9,
                              file_path=path, start_line=line_of(text, m.start())))
        for m in REF_RE.finditer(code):
            table = m.group(1).split(".")[-1]
            if table.lower() in ("select", "where", "set", "values", "dual"):
                continue
            if table not in tables and table not in ("information_schema",):
                tables[table] = ""
        for table, _ in tables.items():
            tkey = f"table:{ctx.repository_name}:{table}"
            res.add(SNode(key=tkey, type="TABLE", name=table, qualified_name=table,
                          properties={"source": "sql-reference"},
                          detection="STATIC_AST", confidence=0.8, file_path=path))
        return res

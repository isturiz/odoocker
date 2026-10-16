"""Read role references from pg_dump SQL, never from COPY rows or SQL literals.

This is deliberately a bounded pg_dump reader, not a general SQL parser. Unsupported
client commands, cluster administration and transaction control fail closed.
"""

import re
from dataclasses import dataclass

from ..errors import OdxError


@dataclass(frozen=True)
class Token:
    value: str
    quoted: bool = False
    literal: bool = False

    def is_keyword(self, value):
        return not self.quoted and self.value == value


DOLLAR = re.compile(r"\$(?:[a-zA-Z_][a-zA-Z0-9_]*)?\$")


def statements(lines):
    tokens = []
    mode = None
    quoted = ""
    delimiter = None
    depth = 0
    copying = False
    size = 0
    escaped_string = False
    for line in lines:
        if copying:
            if line.rstrip("\r\n") == r"\.":
                copying = False
            continue
        size += len(line)
        if size > 2 * 1024 * 1024:
            raise OdxError("SQL statement exceeds supported pg_dump metadata size")
        if mode is None and line.lstrip().startswith("\\"):
            if tokens or not re.fullmatch(r"\s*\\(?:unrestrict|restrict) [a-zA-Z0-9]+\s*", line):
                raise OdxError("Unsupported psql client command in dump")
            continue
        pos = 0
        while pos < len(line):
            size += 1
            if size > 2 * 1024 * 1024:
                raise OdxError("SQL statement exceeds supported pg_dump metadata size")
            char = line[pos]
            pair = line[pos : pos + 2]
            if mode == "comment":
                if pair == "/*":
                    depth += 1
                    pos += 2
                elif pair == "*/":
                    depth -= 1
                    pos += 2
                    if not depth:
                        mode = None
                else:
                    pos += 1
                continue
            if mode == "dollar":
                end = line.find(delimiter, pos)
                if end < 0:
                    break
                pos = end + len(delimiter)
                mode = None
                continue
            if mode in ("identifier", "string"):
                quote = '"' if mode == "identifier" else "'"
                if char == quote:
                    if pair == quote * 2:
                        quoted += quote
                        pos += 2
                    else:
                        if mode == "identifier":
                            tokens.append(Token(quoted, True))
                        else:
                            tokens.append(Token(quoted, True, True))
                        mode = None
                        pos += 1
                elif char == "\\" and mode == "string" and escaped_string:
                    quoted += line[pos : pos + 2]
                    pos += 2
                else:
                    quoted += char
                    pos += 1
                continue
            if pair == "--":
                break
            if pair == "/*":
                mode, depth = "comment", 1
                pos += 2
            elif char.isspace():
                pos += 1
            elif char == '"':
                mode, quoted = "identifier", ""
                pos += 1
            elif char == "'":
                escaped_string = pos > 0 and line[pos - 1] in "eE"
                mode, quoted = "string", ""
                pos += 1
            elif char == "$" and (match := DOLLAR.match(line, pos)):
                delimiter = match.group()
                tokens.append(Token("<body>", True, True))
                mode = "dollar"
                pos = match.end()
            elif char == ";":
                if tokens:
                    statement = tuple(tokens)
                    yield statement
                    if statement[0].is_keyword("copy"):
                        if not (
                            len(statement) >= 3
                            and statement[-2].is_keyword("from")
                            and statement[-1].is_keyword("stdin")
                        ):
                            raise OdxError("Only pg_dump COPY FROM stdin is supported")
                        if line[pos + 1 :].strip():
                            raise OdxError("Unexpected content after COPY declaration")
                        copying = True
                    tokens = []
                size = 0
                pos += 1
            elif char.isalpha() or char == "_":
                end = pos + 1
                while end < len(line) and (line[end].isalnum() or line[end] in "_$"):
                    end += 1
                tokens.append(Token(line[pos:end].lower()))
                pos = end
            else:
                tokens.append(Token(char))
                pos += 1
    if mode or tokens or copying:
        raise OdxError("Incomplete SQL, quoted body, or COPY data in dump")


def _names(tokens):
    names = set()
    expect_name = True
    for token in tokens:
        if expect_name:
            if token.literal:
                raise OdxError("Expected a PostgreSQL role identifier, not a SQL literal")
            if not token.quoted and token.value in (
                "public",
                "current_user",
                "session_user",
                "current_role",
                "default",
                "none",
            ):
                pass
            elif token.quoted or re.fullmatch(r"[^\W\d][\w$]*", token.value):
                if not token.value or "\x00" in token.value or len(token.value.encode()) > 63:
                    raise OdxError("Unsupported PostgreSQL role identifier")
                names.add(token.value)
            else:
                raise OdxError("Unsupported PostgreSQL role list")
        elif token.value != "," or token.quoted:
            raise OdxError("Unsupported PostgreSQL role list")
        expect_name = not expect_name
    if expect_name:
        raise OdxError("Incomplete PostgreSQL role list")
    return names


def _keyword(tokens, value, start=0):
    return next((i for i in range(start, len(tokens)) if tokens[i].is_keyword(value)), None)


def required_roles(lines):
    roles = set()
    for tokens in statements(lines):
        words = [token.value if not token.quoted else None for token in tokens]
        first = words[0]
        if first in ("begin", "commit", "end", "rollback", "abort", "start", "prepare"):
            raise OdxError("Transaction control is not supported inside a restore dump")
        if first in ("create", "alter", "drop") and len(words) > 1:
            if words[1] in ("role", "user", "group", "database", "tablespace", "system"):
                raise OdxError("Cluster administration is not supported inside a restore dump")
        if first == "set" and words[1:3] == ["session", "authorization"]:
            names = tokens[3:]
            if len(names) == 1 and names[0].literal and names[0].value != "<body>":
                if "\\" in names[0].value:
                    raise OdxError("Escaped session authorization names are unsupported")
                names = (Token(names[0].value, True),)
            roles.update(_names(names))
        elif first == "set" and words[1:2] == ["role"]:
            roles.update(_names(tokens[2:]))
        elif first == "alter" and (owner := _keyword(tokens, "owner")) is not None:
            if words[owner + 1 : owner + 2] == ["to"]:
                roles.update(_names(tokens[owner + 2 :]))
        if words[:3] == ["alter", "default", "privileges"]:
            position = _keyword(tokens, "for")
            if position is not None:
                if words[position + 1] not in ("role", "user"):
                    raise OdxError("Unsupported default privileges owner")
                end = next(
                    (
                        i
                        for i in range(position + 2, len(words))
                        if words[i] in ("in", "grant", "revoke")
                    ),
                    len(words),
                )
                roles.update(_names(tokens[position + 2 : end]))
        elif first not in ("grant", "revoke"):
            continue
        if first in ("grant", "revoke") or words[:3] == ["alter", "default", "privileges"]:
            on = _keyword(tokens, "on")
            if on is None:
                raise OdxError("Role membership grants/revokes are not supported in database dumps")
            direction = "from" if "revoke" in words[:on] else "to"
            start = _keyword(tokens, direction, on + 1)
            if start is None:
                raise OdxError("Unsupported object privilege declaration")
            end = next(
                (
                    i
                    for i in range(start + 1, len(words))
                    if words[i] in ("with", "granted", "cascade", "restrict")
                ),
                len(words),
            )
            roles.update(_names(tokens[start + 1 : end]))
            granted = _keyword(tokens, "granted", end)
            if granted is not None and words[granted + 1 : granted + 2] == ["by"]:
                roles.update(_names(tokens[granted + 2 :]))
    return sorted(roles)

"""The exact text every setup's agent receives. Identical across setups; nothing names a tool."""

from __future__ import annotations

STYLE = {  # example path, example method, how to name a method
    "python": ("src/package/module.py", "Class.method", "Name a method by its class, as in `Class.method`."),
    "rust": ("src/module.rs", "Type.method", "Name a method by the type its impl block is for, as in `Type.method`."),
}


def answer_format(answer: str, lang: str) -> str:
    path, method, rule = STYLE[lang]
    if answer == "files":
        other = path.replace("module", "other")
        return (
            "End your reply with a fenced JSON block that lists each file by its path relative to the "
            f'repository root:\n\n```json\n{{"locations": ["{path}", "{other}"]}}\n```'
        )
    return (
        'End your reply with a fenced JSON block that lists each function as "path:Qualified.name", with the '
        f"path relative to the repository root. {rule}\n\n"
        f'```json\n{{"locations": ["{path}:{method}", "{path}:function"]}}\n```'
    )


def localization(problem_statement: str, lang: str) -> str:
    return (
        f"<issue>\n{problem_statement.strip()}\n</issue>\n\n"
        "Find the functions that must change to resolve this issue. Search the repository in the current "
        "directory, and do not edit any files.\n\n" + answer_format("functions", lang)
    )


def structural(question: str, answer: str, lang: str) -> str:
    return (
        f"{question}\n\nSearch the repository in the current directory, and do not edit any files.\n\n"
        + answer_format(answer, lang)
    )

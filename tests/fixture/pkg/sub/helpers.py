import re


def slugify(s):
    return normalize(s).replace(" ", "-")


def normalize(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def unused_helper():
    pass

import os


def test_tests_do_not_inherit_git_env():
    # a commit hook exports GIT_DIR / GIT_INDEX_FILE; see conftest._no_inherited_git_env
    assert [k for k in os.environ if k.startswith("GIT_")] == []

import os


def greet(name: str) -> str:
    # GREETING comes from the env-vars input in the self-test
    return f"{os.environ['GREETING']}, {name}"

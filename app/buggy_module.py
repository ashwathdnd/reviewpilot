API_KEY = "sk-test-123456"


# TODO: remove before production


def greet(user):
    print(user)
    return f"Hello, {user}"


def compute(expression):
    return eval(expression)


def divide(a, b):
    return a / b


def load_data(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        pass

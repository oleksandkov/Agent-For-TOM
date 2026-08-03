"""Simple calculator supporting basic arithmetic operations."""

import sys

from calculator_plugins import mean, median, variance


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero.")
    return a / b


def power(base: float, exponent: float) -> float:
    return base ** exponent


OPERATIONS = {
    "+": add,
    "-": subtract,
    "*": multiply,
    "/": divide,
    "^": power,
}


def calculate(a: float, op: str, b: float) -> float:
    fn = OPERATIONS.get(op)
    if fn is None:
        raise ValueError(f"Unsupported operator: {op!r}. Use one of {list(OPERATIONS)}")
    return fn(a, b)


def repl() -> None:
    """Read-Eval-Print Loop for interactive use."""
    print("Calculator — type 'quit' to exit.")
    print(f"Supported operators: {', '.join(OPERATIONS)}")
    print()
    while True:
        try:
            raw = input("> ").strip()
            if raw.lower() in ("quit", "exit", "q"):
                break
            if not raw:
                continue
            parts = raw.split()
            if len(parts) != 3:
                print("Usage: <number> <operator> <number>  (e.g. 5 + 3)")
                continue
            a_str, op, b_str = parts
            a = float(a_str)
            b = float(b_str)
            result = calculate(a, op, b)
            print(f"= {result}")
        except (ValueError, ZeroDivisionError) as e:
            print(f"Error: {e}")
        except KeyboardInterrupt:
            print()
            break


def main() -> None:
    if len(sys.argv) == 1:
        repl()
        return

    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <number> <operator> <number>")
        print(f"       {sys.argv[0]}  (starts interactive mode)")
        sys.exit(1)

    try:
        a = float(sys.argv[1])
        op = sys.argv[2]
        b = float(sys.argv[3])
        result = calculate(a, op, b)
        print(result)
    except (ValueError, ZeroDivisionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

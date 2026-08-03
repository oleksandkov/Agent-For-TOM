"""Statistical functions as plugins for the calculator."""

from typing import List, Union

Number = Union[int, float]


def mean(numbers: List[Number]) -> float:
    if not numbers:
        raise ValueError("mean() requires at least one number.")
    return sum(numbers) / len(numbers)


def median(numbers: List[Number]) -> float:
    if not numbers:
        raise ValueError("median() requires at least one number.")
    ordered = sorted(numbers)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(numbers: List[Number]) -> float:
    if len(numbers) < 2:
        raise ValueError("variance() requires at least two numbers.")
    m = mean(numbers)
    return sum((x - m) ** 2 for x in numbers) / (len(numbers) - 1)

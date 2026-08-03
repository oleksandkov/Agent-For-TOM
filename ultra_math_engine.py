"""Vector and matrix math engine: dot product, norms, and matmul."""

import math
from typing import List, Sequence, Union

Number = Union[int, float]
Vector = Sequence[Number]
Matrix = Sequence[Sequence[Number]]


def dot_product(a: Vector, b: Vector) -> float:
    """Compute the dot product of two vectors of equal length.

    Raises ValueError if the vectors differ in length or are empty.
    """
    if len(a) != len(b):
        raise ValueError(
            f"dot_product() requires equal-length vectors, got {len(a)} and {len(b)}."
        )
    if not a:
        raise ValueError("dot_product() requires non-empty vectors.")
    return float(sum(x * y for x, y in zip(a, b)))


def vector_norm(v: Vector) -> float:
    """Compute the Euclidean norm (magnitude) of a vector.

    Raises ValueError if the vector is empty.
    """
    if not v:
        raise ValueError("vector_norm() requires a non-empty vector.")
    return math.sqrt(sum(x * x for x in v))


def normalize(v: Vector) -> List[float]:
    """Return the unit vector in the direction of v.

    Raises ValueError if v is empty or has zero magnitude.
    """
    norm = vector_norm(v)
    if norm == 0.0:
        raise ValueError("normalize() requires a non-zero vector.")
    return [x / norm for x in v]


def matmul(A: Matrix, B: Matrix) -> List[List[float]]:
    """Multiply two matrices A (m×k) and B (k×n) to get an m×n result.

    Raises ValueError if matrices are empty, ragged, or dimensions
    are incompatible (A columns != B rows).
    """
    if not A or not B:
        raise ValueError("matmul() requires non-empty matrices.")
    if not A[0] or not B[0]:
        raise ValueError("matmul() requires non-empty rows.")

    k = len(A[0])
    if any(len(row) != k for row in A):
        raise ValueError("matmul() requires A to be rectangular (all rows same length).")
    if any(len(row) != len(B[0]) for row in B):
        raise ValueError("matmul() requires B to be rectangular (all rows same length).")
    if len(B) != k:
        raise ValueError(
            f"matmul() dimension mismatch: A has {k} columns, B has {len(B)} rows."
        )

    n = len(B[0])
    # Transpose B once for cache-friendly column access.
    B_T = list(zip(*B))
    return [
        [float(sum(a * b for a, b in zip(row, col))) for col in B_T]
        for row in A
    ]

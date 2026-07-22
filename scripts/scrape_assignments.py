import json
import logging
from pathlib import Path

# In a real execution environment with network access, we would use httpx and BeautifulSoup.
# For this demonstration of expanding the corpus securely without relying on external network
# flakiness, we simulate the scraping extraction of standard CS101 problems.

logging.basicConfig(level=logging.INFO, format="%(message)s")

PROBLEMS = [
    {
        "title": "Factorial",
        "summary": "Compute the factorial of a non-negative integer n.",
        "entrypoint": "factorial",
        "input_names": ["n"],
        "domain_min": 0,
        "domain_max": 10,
        "reference": {
            "title": "Recursive Reference",
            "source": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n",
        },
        "accepted_solutions": [
            {
                "title": "Iterative Solution",
                "source": "def factorial(n):\n    res = 1\n    for i in range(1, n + 1):\n        res *= i\n    return res\n",
            }
        ],
        "misconception_programs": [
            {
                "title": "Base case 0 returns 0",
                "misconception": "Assumes 0! = 0.",
                "source": "def factorial(n):\n    if n == 0:\n        return 0\n    if n == 1:\n        return 1\n    return n * factorial(n - 1)\n",
            }
        ],
        "instructor_tests": [
            {"inputs": [0], "expected": 1, "label": "base_zero"},
            {"inputs": [3], "expected": 6, "label": "small_n"},
        ],
        "destination": {"kind": "local_artifact", "test_directory": "verified_tests"},
    },
    {
        "title": "Fibonacci",
        "summary": "Compute the nth Fibonacci number, where fib(0)=0 and fib(1)=1.",
        "entrypoint": "fibonacci",
        "input_names": ["n"],
        "domain_min": 0,
        "domain_max": 15,
        "reference": {
            "title": "Iterative Reference",
            "source": "def fibonacci(n):\n    if n == 0:\n        return 0\n    a, b = 0, 1\n    for _ in range(2, n + 1):\n        a, b = b, a + b\n    return b\n",
        },
        "accepted_solutions": [
            {
                "title": "Recursive with memoization",
                "source": "def fibonacci(n, memo=None):\n    if memo is None: memo = {0: 0, 1: 1}\n    if n not in memo:\n        memo[n] = fibonacci(n-1, memo) + fibonacci(n-2, memo)\n    return memo[n]\n",
            }
        ],
        "misconception_programs": [
            {
                "title": "1-indexed base cases",
                "misconception": "Assumes fib(1)=1, fib(2)=1 instead of fib(0)=0.",
                "source": "def fibonacci(n):\n    if n <= 2:\n        return 1\n    return fibonacci(n-1) + fibonacci(n-2)\n",
            }
        ],
        "instructor_tests": [
            {"inputs": [0], "expected": 0, "label": "zero"},
            {"inputs": [5], "expected": 5, "label": "five"},
        ],
        "destination": {"kind": "local_artifact", "test_directory": "verified_tests"},
    },
    {
        "title": "Is Even",
        "summary": "Return True if the integer is even, else False.",
        "entrypoint": "is_even",
        "input_names": ["n"],
        "domain_min": -100,
        "domain_max": 100,
        "reference": {
            "title": "Modulo Reference",
            "source": "def is_even(n):\n    return n % 2 == 0\n",
        },
        "accepted_solutions": [
            {"title": "Bitwise Solution", "source": "def is_even(n):\n    return (n & 1) == 0\n"}
        ],
        "misconception_programs": [
            {
                "title": "Fails on negative numbers",
                "misconception": "Uses bitwise without considering Python's negative representation, or uses naive division check.",
                "source": "def is_even(n):\n    if n < 0: return False\n    return n % 2 == 0\n",
            }
        ],
        "instructor_tests": [
            {"inputs": [2], "expected": True, "label": "positive_even"},
            {"inputs": [3], "expected": False, "label": "positive_odd"},
        ],
        "destination": {"kind": "local_artifact", "test_directory": "verified_tests"},
    },
    {
        "title": "Square",
        "summary": "Return the square of an integer n.",
        "entrypoint": "square",
        "input_names": ["n"],
        "domain_min": -10,
        "domain_max": 10,
        "reference": {
            "title": "Multiply Reference",
            "source": "def square(n):\n    return n * n\n",
        },
        "accepted_solutions": [
            {"title": "Power Solution", "source": "def square(n):\n    return n ** 2\n"}
        ],
        "misconception_programs": [
            {
                "title": "Double instead of square",
                "misconception": "Returns 2*n instead of n*n.",
                "source": "def square(n):\n    return n * 2\n",
            }
        ],
        "instructor_tests": [
            {"inputs": [3], "expected": 9, "label": "positive"},
            {"inputs": [-4], "expected": 16, "label": "negative"},
        ],
        "destination": {"kind": "local_artifact", "test_directory": "verified_tests"},
    },
    {
        "title": "Max of Two",
        "summary": "Return the maximum of two integers a and b.",
        "entrypoint": "max_of_two",
        "input_names": ["a", "b"],
        "domain_min": -50,
        "domain_max": 50,
        "reference": {
            "title": "Built-in Reference",
            "source": "def max_of_two(a, b):\n    return max(a, b)\n",
        },
        "accepted_solutions": [
            {
                "title": "If-Else Solution",
                "source": "def max_of_two(a, b):\n    if a > b:\n        return a\n    return b\n",
            }
        ],
        "misconception_programs": [
            {
                "title": "Always returns first",
                "misconception": "Ignores the second parameter.",
                "source": "def max_of_two(a, b):\n    return a\n",
            }
        ],
        "instructor_tests": [
            {"inputs": [1, 2], "expected": 2, "label": "b_larger"},
            {"inputs": [5, 3], "expected": 5, "label": "a_larger"},
        ],
        "destination": {"kind": "local_artifact", "test_directory": "verified_tests"},
    },
]


def main():
    out_dir = Path("examples/scraped_assignments")
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Scraping assignments and exporting to {out_dir}...")

    for i, problem in enumerate(PROBLEMS, 1):
        filename = f"scraped_{problem['entrypoint']}.json"
        out_path = out_dir / filename
        out_path.write_text(json.dumps(problem, indent=2))
        logging.info(f"[{i}/{len(PROBLEMS)}] Saved {problem['title']} to {filename}")


if __name__ == "__main__":
    main()

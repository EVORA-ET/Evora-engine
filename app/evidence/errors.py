class CalculationError(ValueError):
    """An input that makes a calculation meaningless (wrong sign, impossible
    value, inconsistent units). Raised by the plain-Python calculation layer;
    the API turns it into a 400 naming the field."""

    def __init__(self, field, message):
        super().__init__(f'{field}: {message}')
        self.field = field
        self.message = message


def require_positive(field, value):
    if value is None:
        raise CalculationError(field, 'is required.')
    if not value > 0:
        raise CalculationError(field, f'must be greater than 0 (got {value}).')
    return value


def require_non_negative(field, value):
    if value is None:
        raise CalculationError(field, 'is required.')
    if value < 0:
        raise CalculationError(field, f'must not be negative (got {value}).')
    return value


def require_fraction(field, value, *, allow_zero=True, allow_one=True):
    if value is None:
        raise CalculationError(field, 'is required.')
    low_ok = value >= 0 if allow_zero else value > 0
    high_ok = value <= 1 if allow_one else value < 1
    if not (low_ok and high_ok):
        raise CalculationError(
            field, f'must be a fraction between 0 and 1 (got {value}).')
    return value

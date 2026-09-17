/// Lightweight success/error type for service calls.
sealed class Result<T> {
  const Result();

  factory Result.ok(T value) = Ok<T>;
  factory Result.err(Object error) = Err<T>;

  bool get isOk => this is Ok<T>;
  bool get isErr => this is Err<T>;

  T? get value => switch (this) { Ok(:final value) => value, Err() => null };
  Object? get error => switch (this) { Ok() => null, Err(:final error) => error };
}

class Ok<T> extends Result<T> {
  const Ok(this.value);
  @override
  final T value;
}

class Err<T> extends Result<T> {
  const Err(this.error);
  @override
  final Object error;
}

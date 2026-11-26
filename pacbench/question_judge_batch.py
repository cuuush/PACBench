"""Keep the original paired-batch command and historical reader invocation."""
if __package__:
    from .judging.batch import main
else:
    # Direct script invocations can supply a separate reviewed reader via PYTHONPATH.
    from judging.batch import main


if __name__ == '__main__':
    raise SystemExit(main())

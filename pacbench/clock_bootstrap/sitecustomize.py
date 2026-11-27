"""Install the requested clock before the provider interpreter imports its product."""
import os

if os.environ.get('PACBENCH_PYTHON_CLOCK_FILE'):
    try:
        from pacbench.python_provider_clock import install
        install()
    except BaseException:
        os.write(2, b'PACBench clock bootstrap failed\n')
        os._exit(78)

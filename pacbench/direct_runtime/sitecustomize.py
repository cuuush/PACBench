"""Fail-closed bootstrap for benchmark-owned direct transport instrumentation."""
import os
if os.environ.get('PACBENCH_DIRECT')=='1' or os.environ.get('PACBENCH_PYTHON_CLOCK_FILE'):
    try:
        if os.environ.get('PACBENCH_PYTHON_CLOCK_FILE'):
            from pacbench.python_provider_clock import install as install_clock
            install_clock()
        if os.environ.get('PACBENCH_DIRECT')=='1':
            from pacbench.direct_http import install
            install()
    except BaseException:
        os.write(2,b'PACBench transport/clock bootstrap failed; no inference permitted\n')
        os._exit(78)

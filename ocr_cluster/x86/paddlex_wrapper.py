#!/usr/bin/env python3
"""Thin wrapper around the `paddlex` CLI that caps CPU inference threads"""

import os
import sys


def _install_cpu_thread_cap(num_threads):
    from paddlex.inference.utils.pp_option import PaddlePredictorOption

    original = PaddlePredictorOption._get_default_config

    def _get_default_config(self, model_name):
        config = original(self, model_name)
        config["cpu_threads"] = num_threads
        return config

    PaddlePredictorOption._get_default_config = _get_default_config


def main():
    num_threads = int(os.environ.get("PADDLEX_CPU_THREADS", "0"))
    if num_threads > 0:
        _install_cpu_thread_cap(num_threads)
        print(f"[serve] cpu_threads capped at {num_threads}", flush=True)

    from paddlex.__main__ import console_entry

    return console_entry()


if __name__ == "__main__":
    sys.exit(main())

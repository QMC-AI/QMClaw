"""Portable runner.

Offline by default. Hardware execution requires an explicitly supplied adapter:
    python portable_zpa_pipeline.py manifest.json --execute --adapter my_lab_adapter
"""
from zpa_calibration_pipeline import main
if __name__ == '__main__':
    main()

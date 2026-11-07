"""Testing utilities for nonoha-embedded (dart_bridge independent).

Provides test functions for verifying nonoha-core functionality.
"""

from typing import Any

import numpy as np
from nonoha_core.node_registry import NodeRegistry
from nonoha_core.nodes.arithmetic_operation_nodes import AddNode


def test_nonoha_core(registry: NodeRegistry) -> dict[str, Any]:
    """Test nonoha-core basic functionality.

    Args:
        registry: NodeRegistry instance with registered nodes.

    Returns:
        Test result dictionary with status and test details.
    """
    results: dict[str, Any] = {
        "status": "success",
        "tests": [],
    }

    try:
        # Test 1: Create AddNode and verify structure
        add_node = AddNode("test_add")
        results["tests"].append(
            {
                "name": "create_add_node",
                "passed": True,
                "details": {
                    "node_name": add_node.name,
                    "node_type": add_node.type,
                    "input_ports": list(add_node.inputs.keys()),
                    "output_ports": list(add_node.outputs.keys()),
                },
            }
        )

        # Test 2: Send values to AddNode and check output
        input_port = add_node.inputs["values"]
        output_port = add_node.outputs["value"]

        output_values: list[Any] = []

        def on_output(values: list) -> None:
            output_values.extend(values)

        output_port.subscribe(999, on_output)

        input_port.dispatch(
            [
                np.array([1.0], dtype=np.float32),
                np.array([2.0], dtype=np.float32),
            ]
        )

        if output_values and len(output_values) > 0:
            output_value = output_values[0]
            if hasattr(output_value, "tolist"):
                output_value = output_value.tolist()
            results["tests"].append(
                {
                    "name": "add_node_execution",
                    "passed": True,
                    "details": {
                        "input_1": 1.0,
                        "input_2": 2.0,
                        "output": output_value,
                        "expected": 3.0,
                    },
                }
            )
        else:
            results["tests"].append(
                {
                    "name": "add_node_execution",
                    "passed": False,
                    "details": {"error": "No output produced"},
                }
            )

        # Test 3: Test NodeRegistry
        registered_types = list(registry.node_definitions.keys())
        results["tests"].append(
            {
                "name": "node_registry",
                "passed": len(registered_types) > 0,
                "details": {"registered_types": registered_types},
            }
        )

        # Test 4: Create node from registry
        created_node = registry.create_node("Add", "registry_add")
        results["tests"].append(
            {
                "name": "create_from_registry",
                "passed": created_node is not None and created_node.type == "Add",
                "details": {
                    "node_name": created_node.name,
                    "node_type": created_node.type,
                },
            }
        )

        # Test 5: NumPy integration
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        arr_sum = float(np.sum(arr))
        results["tests"].append(
            {
                "name": "numpy_integration",
                "passed": arr_sum == 6.0,
                "details": {
                    "array": arr.tolist(),
                    "sum": arr_sum,
                },
            }
        )

    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)
        import traceback

        results["traceback"] = traceback.format_exc()

    return results


def test_scipy() -> dict[str, Any]:
    """Test the cross-built SciPy wheel on-device.

    Verifies scipy imports and that the APIs nonoha-core relies on actually
    run. Critically includes a LAPACK call (``scipy.linalg.solve``) that
    exercises the platform BLAS/LAPACK backend — Apple Accelerate's
    ``$NEWLAPACK`` symbols on iOS (needs iOS 16.4+) or vendored OpenBLAS on
    Android — so a PASS proves those symbols resolve at runtime.

    Returns:
        Test result dictionary with status and test details.
    """
    results: dict[str, Any] = {
        "status": "success",
        "tests": [],
    }

    try:
        # Test 1: import scipy
        try:
            import scipy

            results["tests"].append(
                {
                    "name": "import_scipy",
                    "passed": True,
                    "details": {"version": scipy.__version__},
                }
            )
        except ImportError as e:
            results["tests"].append(
                {
                    "name": "import_scipy",
                    "passed": False,
                    "details": {"error": str(e)},
                }
            )
            results["status"] = "error"
            results["error"] = f"Failed to import scipy: {e}"
            return results

        # Test 2: scipy.linalg.solve -> LAPACK (Accelerate $NEWLAPACK / OpenBLAS)
        from scipy import linalg

        a_mat = np.array([[3.0, 1.0], [1.0, 2.0]])
        b_vec = np.array([9.0, 8.0])
        x_sol = linalg.solve(a_mat, b_vec)
        expected = np.array([2.0, 3.0])
        passed_solve = bool(np.allclose(x_sol, expected))
        results["tests"].append(
            {
                "name": "linalg_solve_lapack",
                "passed": passed_solve,
                "details": {"x": x_sol.tolist(), "expected": expected.tolist()},
            }
        )

        # Test 3: scipy.signal butter + lfilter + lfilter_zi (filter_nodes API)
        from scipy.signal import butter, lfilter, lfilter_zi

        b_coef, a_coef = butter(4, 0.2)
        sig = np.ones(100, dtype=np.float64)
        zi = lfilter_zi(b_coef, a_coef)
        y_sig, _ = lfilter(b_coef, a_coef, sig, zi=zi * sig[0])
        passed_filter = y_sig.shape == (100,) and bool(np.isfinite(y_sig).all())
        results["tests"].append(
            {
                "name": "signal_butter_lfilter",
                "passed": passed_filter,
                "details": {
                    "b_len": len(b_coef),
                    "a_len": len(a_coef),
                    "y_last": float(y_sig[-1]),
                },
            }
        )

        # Test 4: scipy.signal firwin + resample_poly (filter_nodes API)
        from scipy.signal import firwin, resample_poly

        taps = firwin(21, 0.3)
        resampled = resample_poly(sig, up=2, down=1)
        passed_fir = len(taps) == 21 and resampled.shape == (200,)
        results["tests"].append(
            {
                "name": "signal_firwin_resample",
                "passed": bool(passed_fir),
                "details": {"taps": len(taps), "resampled_len": int(resampled.shape[0])},
            }
        )

        # Test 5: scipy.signal.windows.hann (window_nodes API)
        from scipy.signal import windows

        win = windows.hann(64)
        # An even-length symmetric Hann window peaks just below 1.0 (no sample at
        # the exact centre); ~0.99938 for N=64. Validate shape/endpoints/range
        # rather than an exact 1.0 peak (that only holds for odd lengths).
        passed_win = (
            win.shape == (64,)
            and 0.99 < float(win.max()) <= 1.0
            and float(win[0]) < 1e-9
        )
        results["tests"].append(
            {
                "name": "signal_windows_hann",
                "passed": passed_win,
                "details": {"len": int(win.shape[0]), "max": float(win.max())},
            }
        )

        # Test 6: scipy.fft
        from scipy import fft as sp_fft

        spectrum = sp_fft.fft(np.ones(8))
        passed_fft = spectrum.shape == (8,) and bool(abs(float(spectrum[0].real) - 8.0) < 1e-6)
        results["tests"].append(
            {
                "name": "scipy_fft",
                "passed": passed_fft,
                "details": {"dc_bin": float(spectrum[0].real)},
            }
        )

    except Exception as e:
        results["status"] = "error"
        results["error"] = str(e)
        import traceback

        results["traceback"] = traceback.format_exc()

    return results

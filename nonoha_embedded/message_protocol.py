"""Message protocol for embedded TCP communication.

Performance optimizations:
- Use np.frombuffer() for zero-copy array creation
- Reuse nonoha-core's port_values_to_bytes for serialization
- Match nonoha-engine/node_flow/node.py handle_endpoint_event pattern
"""

import numpy as np
from nonoha_core.data_type import DataType
from nonoha_core.port import port_values_to_bytes  # Reuse existing serializer

# Message types
MSG_TYPE_JSON = 0x00
MSG_TYPE_EVENT_INPUT = 0x01  # Client→Server: fire-and-forget input
MSG_TYPE_EVENT_OUTPUT = 0x02  # Server→Client: push output event
MSG_TYPE_GET_OUTPUT_RESPONSE = 0x03  # Server→Client: get_output response


def parse_dispatch_input(received_bytes: bytes) -> tuple[str, str, str, list]:
    """Parse DISPATCH_INPUT message.

    Format: [msg_type][flow_name_len][flow_name][graph_name_len][graph_name]
            [event_type][port_name_len][port_name][values...]

    Returns: (flow_name, graph_name, port_name, values)

    Performance: Uses np.frombuffer for zero-copy array creation.
    Based on nonoha-engine/node_flow/node.py handle_endpoint_event.
    """
    offset = 1  # skip msg_type

    # Read flow_name
    flow_name_len = received_bytes[offset]
    offset += 1
    flow_name = received_bytes[offset : offset + flow_name_len].decode()
    offset += flow_name_len

    # Read graph_name
    graph_name_len = received_bytes[offset]
    offset += 1
    graph_name = received_bytes[offset : offset + graph_name_len].decode()
    offset += graph_name_len

    # Skip event_type byte (INPUT=0x00)
    offset += 1

    # Read port_name
    port_name_len = received_bytes[offset]
    offset += 1
    port_name = received_bytes[offset : offset + port_name_len].decode()
    offset += port_name_len

    # Parse values (same pattern as nonoha-engine/node_flow/node.py)
    value_count = received_bytes[offset]
    offset += 1

    values = []
    for _ in range(value_count):
        data_type = DataType(received_bytes[offset])
        offset += 1

        ndim = received_bytes[offset]
        offset += 1

        # Zero-copy shape read
        shape = np.frombuffer(received_bytes, dtype=">u2", count=ndim, offset=offset)
        offset += ndim * 2

        data_size = int.from_bytes(received_bytes[offset : offset + 4], "big")
        offset += 4

        # Zero-copy array creation with np.frombuffer
        match data_type:
            case DataType.Float32:
                arr = np.frombuffer(
                    received_bytes, dtype=">f4", count=data_size // 4, offset=offset
                )
            case DataType.Float64:
                arr = np.frombuffer(
                    received_bytes, dtype=">f8", count=data_size // 8, offset=offset
                )
            case DataType.Int8:
                arr = np.frombuffer(
                    received_bytes, dtype=">i1", count=data_size, offset=offset
                )
            case DataType.Uint8:
                arr = np.frombuffer(
                    received_bytes, dtype=">u1", count=data_size, offset=offset
                )
            case DataType.Int16:
                arr = np.frombuffer(
                    received_bytes, dtype=">i2", count=data_size // 2, offset=offset
                )
            case DataType.Uint16:
                arr = np.frombuffer(
                    received_bytes, dtype=">u2", count=data_size // 2, offset=offset
                )
            case DataType.Int32:
                arr = np.frombuffer(
                    received_bytes, dtype=">i4", count=data_size // 4, offset=offset
                )
            case DataType.Uint32:
                arr = np.frombuffer(
                    received_bytes, dtype=">u4", count=data_size // 4, offset=offset
                )
            case DataType.Int64:
                arr = np.frombuffer(
                    received_bytes, dtype=">i8", count=data_size // 8, offset=offset
                )
            case DataType.Uint64:
                arr = np.frombuffer(
                    received_bytes, dtype=">u8", count=data_size // 8, offset=offset
                )
            case DataType.Bool:
                arr = np.frombuffer(
                    received_bytes, dtype=">u1", count=data_size, offset=offset
                ).astype(bool)
            case DataType.String:
                byte_data = received_bytes[offset : offset + data_size]
                if byte_data:
                    parts = byte_data.split(b"\x00")
                    strings = [s.decode("utf-8") for s in parts[:-1]]
                else:
                    strings = []
                arr = np.array(strings, dtype=np.dtypes.StringDType())  # type: ignore
            case _:
                raise ValueError(f"Unsupported data type: {data_type}")

        arr = arr.reshape(-1, *shape)
        values.append(arr)
        offset += data_size

    return flow_name, graph_name, port_name, values


def build_get_output_response(
    values: list, value_types: list, buf: bytearray | None = None
) -> bytes:
    """Build GET_OUTPUT_RESPONSE binary message.

    Args:
        values: List of numpy arrays
        value_types: List of PortValueType
        buf: Optional pre-allocated bytearray for reuse

    Uses nonoha-core's port_values_to_bytes for serialization.
    """
    if buf is None:
        buf = bytearray()
    else:
        buf.clear()

    buf.append(MSG_TYPE_GET_OUTPUT_RESPONSE)
    buf.append(0x00)  # success status
    port_values_to_bytes(values, value_types, buf)
    return bytes(buf)


def build_get_output_error() -> bytes:
    """Build GET_OUTPUT_RESPONSE error message."""
    return bytes([MSG_TYPE_GET_OUTPUT_RESPONSE, 0x01])


def build_event_output(
    flow_name: str, graph_name: str, port_name: str, values: list, value_types: list
) -> bytes:
    """Build EVENT_OUTPUT binary message for push notification.

    Format:
        [1] msg_type = 0x02
        [1] flow_name_len
        [K] flow_name (UTF-8)
        [1] graph_name_len
        [N] graph_name (UTF-8)
        [1] port_name_len
        [M] port_name (UTF-8)
        [...] port_values (nonoha-core format)
    """
    buf = bytearray()
    buf.append(MSG_TYPE_EVENT_OUTPUT)

    fn = flow_name.encode("utf-8")
    buf.append(len(fn))
    buf.extend(fn)

    gn = graph_name.encode("utf-8")
    buf.append(len(gn))
    buf.extend(gn)

    pn = port_name.encode("utf-8")
    buf.append(len(pn))
    buf.extend(pn)

    port_values_to_bytes(values, value_types, buf)
    return bytes(buf)

# Copyright 2020 - 2025 Ternaris
# SPDX-License-Identifier: Apache-2.0
"""
This is the converter.py script from rosbags (https://gitlab.com/ternaris/rosbags)

Modified to add custom messages from multiple packages.

The only change is the addition of the main function.
"""

from __future__ import annotations

from functools import partial
import os
from typing import TYPE_CHECKING, cast

from pathlib import Path
import numpy as np

from rosbags.highlevel.anyreader import AnyReader, AnyReaderError
from rosbags.interfaces import (
    Connection,
    ConnectionExtRosbag1,
    ConnectionExtRosbag2,
    Nodetype,
    Qos,
    QosDurability,
    QosHistory,
    QosLiveliness,
    QosReliability,
    QosTime,
)
from rosbags.rosbag1 import (
    Writer as Writer1,
    WriterError as WriterError1,
)
from rosbags.rosbag2 import (
    StoragePlugin,
    Writer as Writer2,
    WriterError as WriterError2,
)
from rosbags.typesys import Stores, get_types_from_msg, get_typestore

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Literal

    from rosbags.typesys.store import Msg, Msgarg, Typestore

from generate_message_types import generate_message_dict


LATCH = [
    Qos(
        QosHistory.UNKNOWN,
        0,
        QosReliability.RELIABLE,
        QosDurability.TRANSIENT_LOCAL,
        QosTime(2147483647, 4294967295),
        QosTime(2147483647, 4294967295),
        QosLiveliness.AUTOMATIC,
        QosTime(2147483647, 4294967295),
        avoid_ros_namespace_conventions=False,
    )
]

# Legacy message types that will always be renamed
STATIC_MSGTYPE_RENAMES = {
    'tf/msg/tfMessage': 'tf2_msgs/msg/TFMessage',
}


class ConverterError(Exception):
    """Converter Error."""


def echo(text: str) -> None:
    """Print text."""
    print(text)  # noqa: T201


def is_same_wireformat(
    src_typestore: Typestore,
    dst_typestore: Typestore,
    src_msgtype: str,
    dst_msgtype: str,
) -> bool:
    """Check if wireformat of messages is identical."""
    if src_msgtype == 'std_msgs/msg/Header':
        return True
    src_fields = src_typestore.fielddefs[src_msgtype][1]
    dst_fields = dst_typestore.fielddefs[dst_msgtype][1]

    if len(src_fields) != len(dst_fields):
        return False

    for (_, src), (_, dst) in zip(src_fields, dst_fields, strict=True):
        if src != dst:
            return False

        sub_src = src[1][0] if src[0] == Nodetype.SEQUENCE or src[0] == Nodetype.ARRAY else src
        sub_dst = dst[1][0] if dst[0] == Nodetype.SEQUENCE or dst[0] == Nodetype.ARRAY else dst
        if sub_src[0] == Nodetype.NAME:
            assert isinstance(sub_dst[1], str)
            if not is_same_wireformat(src_typestore, dst_typestore, sub_src[1], sub_dst[1]):
                return False

    return True


def default_message(
    typestore: Typestore,
    msgtype: str,
) -> object:
    """Create default message."""
    values: list[Msgarg] = []

    for _, typ in typestore.fielddefs[msgtype][1]:
        if typ[0] == Nodetype.BASE:
            values.append('' if typ[1][0] == 'string' else 0)
        elif typ[0] == Nodetype.NAME:
            values.append(cast('Msg', default_message(typestore, typ[1])))
        else:
            assert typ[0] in {Nodetype.SEQUENCE, Nodetype.ARRAY}
            subtyp = typ[1][0]
            size = 0 if typ[0] == Nodetype.SEQUENCE else typ[1][1]
            if subtyp[0] == Nodetype.BASE:
                if subtyp[1][0] == 'string':
                    values.append([''] * size)
                else:
                    dtype = 'uint8' if subtyp[1][0] == 'char' else subtyp[1][0]
                    values.append(np.zeros(size, dtype=np.dtype(dtype)))
            else:
                values.append([cast('Msg', default_message(typestore, subtyp[1]))] * size)

    return typestore.types[msgtype](*values)


def migrate_message(
    src_typestore: Typestore,
    dst_typestore: Typestore,
    src_msgtype: str,
    dst_msgtype: str,
    cache: dict[str, object],
    src_msg: object,
) -> object:
    """Migrate message."""
    values: list[Msgarg] = []

    src_def = src_typestore.fielddefs[src_msgtype][1]
    dst_def = dst_typestore.fielddefs[dst_msgtype][1]

    src_map = dict(src_def)
    dst_map = dict(dst_def)

    dst_src = {x: x for x in dst_map if x in src_map}

    removed = [x[0] for x in src_def if x[0] not in dst_map]
    added = [x[0] for x in dst_def if x[0] not in src_map]
    for dst_name in added:
        for src_name in removed:
            if src_map[src_name] == dst_map[dst_name]:
                dst_src[dst_name] = src_name
                removed.remove(src_name)
                break
        else:
            dst_src[dst_name] = '__missing__'

    if dst_msgtype not in cache:
        cache[dst_msgtype] = default_message(dst_typestore, dst_msgtype)
    def_msg = cache[dst_msgtype]

    for dst_name, dst_type in dst_map.items():
        src_name = dst_src[dst_name]
        if src_name == '__missing__':
            values.append(cast('Msgarg', getattr(def_msg, dst_name)))
            continue

        src_type = src_map[src_name]

        if dst_type[0] == Nodetype.BASE:
            if src_type[0] == Nodetype.BASE and (dst_type[1][0] == 'string') == (
                src_type[1][0] == 'string'
            ):
                values.append(cast('Msgarg', getattr(src_msg, src_name)))
            else:
                values.append(cast('Msgarg', getattr(def_msg, src_name)))
        elif dst_type[0] == Nodetype.NAME:
            if src_type[0] == Nodetype.NAME:
                values.append(
                    cast(
                        'Msg',
                        migrate_message(
                            src_typestore,
                            dst_typestore,
                            src_type[1],
                            dst_type[1],
                            cache,
                            cast('Msgarg', getattr(src_msg, src_name)),
                        ),
                    ),
                )
            else:
                values.append(cast('Msgarg', getattr(def_msg, src_name)))
        else:
            assert dst_type[0] in {Nodetype.SEQUENCE, Nodetype.ARRAY}
            src_sub = src_type[1][0]
            dst_sub = dst_type[1][0]
            if src_sub[0] != dst_sub[0]:
                values.append(cast('Msgarg', getattr(def_msg, src_name)))
            else:
                size = dst_type[1][1]
                src_value = cast(
                    'list[str] | list[Msg] | np.ndarray[tuple[int, ...], np.dtype[np.uint8]]',
                    getattr(src_msg, src_name),
                )
                if size and size < len(src_value):
                    src_value = src_value[:size]
                if dst_sub[0] == Nodetype.BASE and dst_sub[1][0] != 'string':
                    dtype = 'uint8' if dst_sub[1][0] == 'char' else dst_sub[1][0]
                    assert not isinstance(src_value, list)
                    src_value = src_value.astype(dtype)
                elif dst_sub[0] == Nodetype.NAME:
                    assert isinstance(src_sub[1], str)
                    src_value = [
                        cast(
                            'Msg',
                            migrate_message(
                                src_typestore,
                                dst_typestore,
                                src_sub[1],
                                dst_sub[1],
                                cache,
                                x,
                            ),
                        )
                        for x in src_value
                    ]
                if dst_type[0] == Nodetype.ARRAY and len(src_value) != size:
                    oldsize = len(src_value)
                    if isinstance(src_value, np.ndarray):
                        src_value = np.resize(src_value, size)
                        src_value[oldsize:] = 0
                    else:
                        src_value += [getattr(def_msg, dst_name)] * (size - oldsize)
                values.append(src_value)

    return dst_typestore.types[dst_msgtype](*values)


def migrate_bytes(
    src_typestore: Typestore,
    dst_typestore: Typestore,
    src_msgtype: str,
    dst_msgtype: str,
    cache: dict[str, object],
    data: bytes | memoryview,
    *,
    src_is2: bool,
    dst_is2: bool,
) -> memoryview:
    """Migrate message."""
    src_msg = (
        src_typestore.deserialize_cdr(data, src_msgtype)
        if src_is2
        else src_typestore.deserialize_ros1(data, src_msgtype)
    )

    dst_msg = migrate_message(
        src_typestore,
        dst_typestore,
        src_msgtype,
        dst_msgtype,
        cache,
        src_msg,
    )

    return (
        dst_typestore.serialize_cdr(dst_msg, dst_msgtype, little_endian=True)
        if dst_is2
        else dst_typestore.serialize_ros1(dst_msg, dst_msgtype)
    )


def generate_message_converter(
    src_typestore: Typestore,
    dst_typestore: Typestore,
    src_msgtype: str,
    dst_msgtype: str,
    cache: dict[str, object],
    *,
    src_is2: bool,
    dst_is2: bool,
) -> Callable[[bytes | memoryview], memoryview]:
    """Generate message converter."""
    if is_same_wireformat(src_typestore, dst_typestore, src_msgtype, dst_msgtype):
        if src_is2 == dst_is2:
            return lambda x: memoryview(x)
        if src_is2:
            return partial(src_typestore.cdr_to_ros1, typename=src_msgtype)
        return partial(dst_typestore.ros1_to_cdr, typename=dst_msgtype)

    echo(f'Msgtype will be migrated: {dst_msgtype}')
    return partial(
        migrate_bytes,
        src_typestore,
        dst_typestore,
        src_msgtype,
        dst_msgtype,
        cache,
        src_is2=src_is2,
        dst_is2=dst_is2,
    )


def create_connections_converters(
    connections: Sequence[Connection],
    typestore: Typestore | None,
    reader: AnyReader,
    writer: Writer1 | Writer2,
) -> tuple[
    dict[tuple[int, object], Connection],
    dict[str, Callable[[bytes | memoryview], memoryview]],
]:
    """Create writer connections and return mapping."""
    is2 = isinstance(writer, Writer2)

    connmap: dict[tuple[int, object], Connection] = {}
    convmap: dict[str, Callable[[bytes | memoryview], memoryview]] = {}

    cache: dict[str, object] = {}

    if not typestore:
        if reader.is2 == is2:
            typestore = reader.typestore
        else:
            header = get_typestore(Stores.ROS2_FOXY if is2 else Stores.ROS1_NOETIC).fielddefs[
                'std_msgs/msg/Header'
            ]
            typestore = get_typestore(Stores.EMPTY)
            typestore.register(
                {
                    **reader.typestore.fielddefs,
                    'std_msgs/msg/Header': header,
                },
            )

    for rconn in connections:
        topic = rconn.topic
        msgtype = STATIC_MSGTYPE_RENAMES.get(rconn.msgtype, rconn.msgtype)

        if rconn.msgtype not in convmap:
            if msgtype not in typestore.fielddefs:
                echo(f'Missing msgtype in destination, copying from source: {msgtype!r}')
                typs = get_types_from_msg(
                    reader.typestore.generate_msgdef(
                        rconn.msgtype,
                        ros_version=2 if is2 else 1,
                    )[0],
                    msgtype,
                )
                _ = typs.pop('std_msgs/msg/Header', None)
                typestore.register(typs)
            convmap[rconn.msgtype] = generate_message_converter(
                reader.typestore,
                typestore,
                rconn.msgtype,
                msgtype,
                cache,
                src_is2=reader.is2,
                dst_is2=is2,
            )

        if isinstance(writer, Writer2):
            ext2 = (
                rconn.ext
                if isinstance(rconn.ext, ConnectionExtRosbag2)
                else ConnectionExtRosbag2('cdr', LATCH if rconn.ext.latching else [])
            )
            for conn in writer.connections:
                if topic == conn.topic and msgtype == conn.msgtype and conn.ext == ext2:
                    break
            else:
                conn = writer.add_connection(
                    topic,
                    msgtype,
                    typestore=typestore,
                    serialization_format=ext2.serialization_format,
                    offered_qos_profiles=ext2.offered_qos_profiles,
                )
        else:
            ext1 = (
                rconn.ext
                if isinstance(rconn.ext, ConnectionExtRosbag1)
                else ConnectionExtRosbag1(
                    None,
                    int(any(x.durability.value == 1 for x in rconn.ext.offered_qos_profiles)),
                )
            )
            for conn in writer.connections:
                if topic == conn.topic and msgtype == conn.msgtype and conn.ext == ext1:
                    break
            else:
                conn = writer.add_connection(
                    topic,
                    msgtype,
                    typestore=typestore,
                    callerid=ext1.callerid,
                    latching=ext1.latching,
                )
        connmap[(rconn.id, rconn.owner)] = conn

    return connmap, convmap


def convert(
    srcs: Sequence[Path],
    dst: Path,
    dst_storage: str,
    dst_version: int,
    compress: str | None,
    compress_mode: str,
    default_typestore: Typestore | None,
    typestore: Typestore | None,
    exclude_topics: Sequence[str],
    include_topics: Sequence[str],
    exclude_msgtypes: Sequence[str],
    include_msgtypes: Sequence[str],
) -> None:
    """Convert between Rosbag1 and Rosbag2.

    Args:
        srcs: Rosbag files to read from.
        dst: Destination path to write rosbag to.
        dst_storage: Destination file storage backend.
        dst_version: Destination file format version.
        compress: Compression algorithm ``bz2`` or ``lz4`` for rosbag1,
            ``zstd`` for rosbag2.
        compress_mode: Compression mode ``file`` or ``message`` rosbag2.
        default_typestore: Default typestore if source files have not message definitions.
        typestore: Convert messages to this destination typestore.
        exclude_topics: Topics to exclude from conversion, even if included explicitly.
        include_topics: Topics to include in conversion, instead of all.
        exclude_msgtypes: Message types to exclude from conversion, even if included explicitly.
        include_msgtypes: Message types to include in conversion, instead of all.

    ExcGroups:
        topics: Select topics to exclude or include. (options are mutually exclusive)

    """
    try:
        writer: Writer1 | Writer2
        is2 = dst.suffix != '.bag'
        if is2:
            storage_plugin = StoragePlugin.MCAP if dst_storage == 'mcap' else StoragePlugin.SQLITE3
            writer = Writer2(
                dst,
                version=cast('Literal[8, 9]', dst_version),
                storage_plugin=storage_plugin,
            )
            if compress:
                writer.set_compression(
                    writer.CompressionMode[compress_mode.upper()],
                    writer.CompressionFormat[compress.upper()],
                )
        else:
            writer = Writer1(dst)
            if compress:
                writer.set_compression(writer.CompressionFormat[compress.upper()])

        with (
            AnyReader(srcs, default_typestore=default_typestore) as reader,
            writer,
        ):
            connections = [
                x
                for x in reader.connections
                if x.topic not in exclude_topics
                and x.msgtype not in exclude_msgtypes
                and (
                    x.topic in include_topics or x.msgtype in include_msgtypes
                    if include_topics and include_msgtypes
                    else x.topic in include_topics
                    if include_topics
                    else x.msgtype in include_msgtypes
                    if include_msgtypes
                    else True
                )
            ]
            if not connections:
                return

            connmap, convmap = create_connections_converters(
                connections,
                typestore,
                reader,
                writer,
            )
            for rconn, timestamp, data in reader.messages(connections=connections):
                writer.write(
                    connmap[(rconn.id, rconn.owner)],
                    timestamp,
                    convmap[rconn.msgtype](data),
                )

    except AnyReaderError as err:
        msg = f'Reading source bag: {err}'
        raise ConverterError(msg) from err
    except (WriterError1, WriterError2) as err:
        msg = f'Writing destination bag: {err}'
        raise ConverterError(msg) from err
    except Exception as err:
        msg = f'Converting rosbag: {err!r}'
        raise ConverterError(msg) from err


import argparse
from tqdm import tqdm

parser = argparse.ArgumentParser(description='Convert ROS1 bags to ROS2 mcap files')
parser.add_argument("--src", help="Source bag file or the directory to convert", required=True)
parser.add_argument("--dst", help="Destination folder to store the mcap files", required=True)
parser.add_argument("--custom_msgs_paths", nargs="+", help="Path to custom message packages")

args = parser.parse_args()

def main() -> None:
    typestore = get_typestore(Stores.ROS2_JAZZY)
    for path in args.custom_msgs_paths:
        messages = generate_message_dict(path)

        for name, message in messages.items():
            typestore.register(get_types_from_msg(message, name))

    if args.src.endswith('.bag'):
        srcs = [Path(args.src)]
    else:
        srcs = []
        for path in Path(args.src).glob('*.bag'):
            if path.is_file():
                srcs.append(path)

        total_size = sum(os.path.getsize(fp) for fp in srcs)
        with tqdm(total=total_size, unit='B', unit_scale=True, desc="Processing") as pbar:
            for src in tqdm(srcs, desc="Converting bags", unit="bag"):
                print("Converting: ", src)
                file_size = os.path.getsize(src)
                try:
                    convert(
                        srcs=[src],
                        dst=Path(args.dst + "/" + src.stem),
                        dst_storage='mcap',
                        dst_version=8,
                        compress="ZSTD",
                        compress_mode='storage',
                        default_typestore=get_typestore(Stores.ROS1_NOETIC),
                        typestore=typestore,
                        exclude_topics=[],
                        include_topics=None,
                        exclude_msgtypes=[],
                        include_msgtypes=None,
                    )
                except Exception as err:
                    print(f'Error converting {src}: {err}')
                pbar.update(file_size)

if __name__ == '__main__':
    main()
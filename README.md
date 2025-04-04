# ROS1 to ROS2 MCAP converter

This is just a wrapper around the [converter.py file from rosbags](https://gitlab.com/ternaris/rosbags/-/blob/master/src/rosbags/convert/converter.py?ref_type=heads), that allows you to insert custom message types into the typestore.

## Usage

```
python convert.py --src path_to_bags --dst output_folder --custom_msgs_paths path_to_custom_message_package1 path_to_custom_message_package2 path_to_custom_message_package3
```
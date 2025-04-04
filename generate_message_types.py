import os

def generate_message_dict(base_path):
	"""
	Generate a dictionary with keys as 'parent_folder_name/msg/message_name' (without extension)
	and values as the contents of the files in the 'msg' folder.

	Args:
		base_path (str): The base path to check for the 'msg' folder.

	Returns:
		dict: A dictionary with the specified structure and file contents.
	"""
	message_dict = {}

	# Check if the 'msg' folder exists
	msg_folder_path = os.path.join(base_path, "msg")
	if not os.path.isdir(msg_folder_path):
		return message_dict

	# Iterate over all files in the 'msg' folder
	for file_name in os.listdir(msg_folder_path):
		file_path = os.path.join(msg_folder_path, file_name)

		# Ensure it's a file
		if os.path.isfile(file_path):
			# Extract the parent folder name and message name without extension
			parent_folder_name = os.path.basename(base_path.rstrip('/'))
			message_name = os.path.splitext(file_name)[0]
			key = f"{parent_folder_name}/msg/{message_name}"

			# Read the contents of the file
			with open(file_path, 'r') as file:
				file_contents = file.read()

			# Add to the dictionary
			message_dict[key] = file_contents

	return message_dict
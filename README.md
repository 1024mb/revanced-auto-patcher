## Dependencies:

- requests
- playwright

If first use you have to run this with `--init` for the configuration file to be created. Afterwards it will
download the latest tools (ReVanced CLI, Patches, Integrations), then the YouTube APK file and finally it will start
patching the APK.

You can also specify some options:

* If running `--init`:
    1. With `--conf` where the configuration file should be stored. This must be a path to a file.
    2. With `--store-path` where all the files will be downloaded. This must be a path to a directory.
    3. With `--output` where the patched APK will be stored. This must be a path to a directory.
* If **not** running `--init`:
    1. With `--conf` where the configuration file should be loaded from. It must be a path to a file.

Default values: [*all located in the current working directory (where the terminal/cmd window is initiated)*]

* `--conf`: "auto-path.json" file.
* `--store-path`: "\tmp" directory.
* `--output`: "\output" directory.

*YouTube apk file is downloaded from Apk-pure.*

## Dependencias:

- requests
- playwright

Si es la primera vez que lo usa, debe ejecutarlo con `--init` para que se cree el archivo de configuración. Posteriormente, descargará las herramientas más recientes (ReVanced CLI, Patches, Integrations), luego el archivo APK de YouTube y finalmente comenzará el parcheo del APK.

También puede especificar algunas opciones:

* Si ejecuta `--init`:
    1. Con `--conf` donde se debe almacenar el archivo de configuración. Debe ser la ruta a un archivo.
    2. Con `--store-path` donde se descargarán todos los archivos. Debe ser la ruta a un directorio.
    3. Con `--output` donde se almacenará el APK parcheado. Debe ser la ruta a un directorio.
* Si **no** ejecuta `--init`:
    1. Con `--conf` desde donde se debe cargar el archivo de configuración. Debe ser la ruta a un archivo.

Valores predeterminados: [*todos ubicados en el directorio de trabajo actual (donde se inició la ventana de terminal/cmd)*]

* `--conf`: archivo "auto-path.json".
* `--store-path`: directorio "\tmp".
* `--output`: directorio "\output".

*El archivo APK de YouTube se descarga desde Apk-pure.*

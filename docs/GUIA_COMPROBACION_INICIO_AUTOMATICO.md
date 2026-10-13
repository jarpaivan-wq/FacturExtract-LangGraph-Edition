# Guía de comprobación: inicio automático con Windows (PC limpio / máquina virtual)

Objetivo: confirmar que FacturExtract v2 arranca solo al iniciar sesión, queda oculto en la bandeja, no duplica nada y se
puede quitar sin dejar restos. Tiempo: unos 20 minutos. Hace falta Python 3.10+ con el «py launcher» ya instalado.

> Todo se hace con el usuario normal (no administrador). Cada paso dice qué debe verse. Si algo no coincide,
> copiar `FacturExtract_datos\logs\arranque.log` y `app.log`: dicen qué pasó, a qué hora y con qué ruta.

## 0. Preparación
1. Copiar la carpeta de entrega a, por ejemplo, `C:\Users\<usuario>\Documents\FacturExtract` (mejor con un espacio en la
   ruta, p. ej. `Mis Documentos`, para probar las comillas).
2. Instantánea de la máquina virtual («PC limpio con carpeta copiada»), para volver atrás.

## 1. Primera ejecución
1. Doble clic en **Iniciar FacturExtract.bat**. Debe instalar solo (varios minutos), pedir la clave de API y abrir la ventana.
2. Debe aparecer la pregunta **«¿Quiere que FacturExtract se abra solo al encender el computador?»** → **Sí**.
3. Comprobar: existe `FacturExtract v2.lnk` en el Escritorio y la casilla **«Iniciar con Windows»** (arriba a la derecha)
   está marcada.
4. Registro (PowerShell, solo lectura):
   ```powershell
   (Get-Item HKCU:\Software\Microsoft\Windows\CurrentVersion\Run).GetValue("FacturExtractV2")
   ```
   Debe mostrar UNA línea con el formato `"…\FacturExtract_datos\entorno\Scripts\pythonw.exe" "…\FacturExtract\FacturExtract.pyw" --oculto`
   (ambas rutas entre comillas). No debe existir ningún valor `FacturExtract` (sin «V2»).
5. Ver `FacturExtract_datos\logs\arranque.log`: debe haber líneas `arranque`, `servicio`, `bandeja`, `ventana`.

## 2. Reinicio (lo importante)
1. **Cerrar la aplicación de verdad:** clic derecho en el ícono de la bandeja → **Salir**.
2. **Reiniciar Windows** e iniciar sesión.
3. Esperar ~30 segundos. Debe verse:
   - el **ícono verde en la bandeja** (puede estar en el menú `^` de iconos ocultos), **sin ventana** abierta;
   - en `arranque.log` una línea `arranque` con la hora del inicio de sesión, `args=--oculto`, y `ventana | estado=oculta en la bandeja`.
4. Clic en el ícono → se abre la ventana. Cerrar con la X → se oculta (sigue en la bandeja). Menú → **Salir** → desaparece.

## 3. Sin duplicados ni ventanas inesperadas
1. Con la app residente (oculta), abrir el acceso directo del Escritorio → **la misma ventana** aparece; no hay una segunda
   copia (Administrador de tareas: un solo `pythonw.exe` de FacturExtract y su hijo).
2. Con la app residente, ejecutar a mano `"…\pythonw.exe" "…\FacturExtract.pyw" --oculto` → no aparece ninguna ventana y
   `arranque.log` dice `segunda instancia | resultado=ya hay una abierta; arranque oculto, no se muestra nada`.

## 4. Alertas tras el reinicio / al despertar
1. Registrar una factura pendiente con vencimiento dentro de 2 días (alerta para hoy). Cerrar con **Salir**.
2. Reiniciar. Pasada la hora de los avisos (09:00 por defecto; se cambia en **Avisos…**), la ventana **Vencimientos del día**
   aparece sola con esa factura; antes de esa hora solo se ve el contador en el botón y en el menú de la bandeja.
3. Suspender el equipo 1 minuto y despertarlo: en menos de ~20 segundos debe revisar (el contador se actualiza).

## 5. Sin red al iniciar
1. Desconectar la red (modo avión) y reiniciar. La app debe arrancar igual (ícono en la bandeja, `servicio` en el log).
2. Conectar la red y usar **Probar conexión** (menú de la bandeja o botón de la ventana): debe dar «Conexión correcta».

## 6. Actualización (reemplazo de la carpeta)
1. Salir de la app. Renombrar la carpeta `FacturExtract` a `FacturExtract_vieja` y copiar la versión nueva con el nombre original.
   → La entrada de inicio sigue valiendo (misma ruta). Reiniciar: arranca normal.
2. Prueba de ruta distinta: copiar la versión nueva a **otra** carpeta (p. ej. `FacturExtract2`) y abrir **su**
   `Iniciar FacturExtract.bat`. Debe quedar en `arranque.log`: `inicio con Windows | estado=reparado` y el valor del registro
   apuntar a la carpeta nueva; el acceso directo del Escritorio también se recrea apuntando a la nueva.
3. Si tras una actualización falta instalar dependencias, el log dice `AVISO dependencias desactualizadas` → ejecutar
   **Iniciar FacturExtract.bat**.

## 7. Desactivar y quitar sin restos
1. Desmarcar la casilla **«Iniciar con Windows»** (o el mismo ítem en el menú de la bandeja). El valor `FacturExtractV2`
   debe desaparecer del registro. Reiniciar: la app ya no arranca sola.
2. Volver a marcarla, y luego **Salir** y ejecutar `"…\pythonw.exe" "…\FacturExtract.pyw" --quitar-inicio`: quita la entrada
   de inicio y `FacturExtract v2.lnk` del Escritorio (lo anota en `arranque.log`). No toca datos ni la carpeta de la app.
3. Para desinstalar del todo, después borrar a mano: la carpeta de la aplicación y `FacturExtract_datos`
   (esta última tiene la base de datos, los Excel y el entorno; hacer respaldo antes si hace falta).
4. Verificación final de que no queda nada:
   ```powershell
   (Get-Item HKCU:\Software\Microsoft\Windows\CurrentVersion\Run).GetValueNames() | Where-Object { $_ -like "*Factur*" }
   Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" | Where-Object Name -like "*Factur*"
   Get-ScheduledTask | Where-Object { $_.Actions.Execute -like "*FacturExtract*" }
   Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "*FacturExtract*" }
   ```
   Los cuatro comandos deben salir vacíos.

## Qué registra cada arranque (`logs\arranque.log`)
Una línea por evento: `fecha hora | pid | evento | detalles`. Eventos: `arranque` (intérprete, script, carpeta de trabajo,
argumentos, carpeta de datos, modo), `servicio` (clave de API presente/AUSENTE, sin mostrarla), `inicio con Windows`
(estado y comando), `bandeja`, `ventana`, `segunda instancia`, `ERROR al arrancar`/`ERROR fatal` (con la traza) y `salida`.
El archivo se recorta solo al pasar de ~256 KB.

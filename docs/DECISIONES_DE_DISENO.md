# FacturExtract v2 (LangGraph): decisiones de diseño

Actualizado el 2026-10-12 (tras la revisión recursiva del diseño). Repo v1: github.com/jarpaivan-wq/FacturExtract (v1.2, archivo único facturextract.py). Se conserva intacto y funcionando; la v2 va en un repo nuevo y vacío, con la v1 solo como referencia de lectura. Diagrama vigente: facturextract_v2_flujo_propuesto.mermaid y .png.

## Objetivo
Proyecto vehículo para aprender LangGraph y LangSmith desde cero, construyendo un workflow real, con Claude Code como copiloto. Sirve también al cliente real de la v1.

## Requisitos de uso (cliente)
- Corre en el PC del cliente (Windows), todo local, sin servicios. Studio y langgraph dev solo en desarrollo; LangSmith opcional y apagado en el cliente (las facturas reales traen RUTs y montos; en desarrollo, solo facturas anonimizadas).
- El cliente ya tiene VSCode y Python. Mínima fricción: acceso directo con doble clic; la primera vez prepara el entorno, instala dependencias y pide la clave de API; crea las carpetas de trabajo; botón "Probar conexión"; errores en lenguaje normal; versiones de librerías fijadas. Actualización: reemplazar la carpeta de la aplicación manteniendo aparte la de datos. Por verificar: versión de Python del cliente frente a la mínima que exija LangGraph (el lanzador debe revisarla y avisar claro).
- Certificados: almacén de certificados de Windows (nada de verify=False como en la v1).
- Aplicación residente: inicia con Windows y queda abierta (bandeja, una sola instancia).

## Decisiones cerradas por Iván
- Entrada: el usuario selecciona las fotos (agrupa 2 si son una sola factura) y pulsa PROCESAR. No hay carpeta vigilada.
- "Mes en curso" = fecha de EMISIÓN, evaluado al registrar la factura. El seguimiento sigue vigente aunque el vencimiento (hasta 30 días) caiga en otro mes. Consecuencia aceptada: una factura de un mes anterior cargada tarde no genera alerta.
- Alerta 3 días CORRIDOS antes del vencimiento; si se carga con vencimiento a menos de 3 días o vencida, alerta de inmediato.
- Acciones: Posponer (fecha posterior), Marcar pagada y Cerrar (reaparece al día siguiente). N° de documento solo cuando se paga con cheque (para registro y consulta); se escribe en todas las filas de la factura junto con el estado y la fecha de pago.
- Facturas al contado: la fecha de vencimiento no es obligatoria y NO se crea seguimiento (aunque traigan fecha). En el Excel el estado de pago se muestra como contado.
- Excel: SOLO LECTURA, regenerado por el programa desde la base, por mes de la fecha de emisión (idempotente, escritura atómica; si el archivo está abierto queda marcado como desactualizado y se reintenta). Va en una carpeta nueva y no toca los archivos de la v1 (la v1 los nombraba por fecha de procesamiento).
- Duplicados (mismo RUT emisor, folio y tipo de documento): se bloquean y se avisa; no se guarda ni se duplican filas ni alertas. Restricción única en la base como última defensa.
- Alertas: una ventana "Vencimientos del día" con la lista, cada fila con Posponer, Pagada y Cerrar. Se abre sola la primera vez del día que haya pendientes (hora configurable, 09:00 por defecto); el resto del día queda en la bandeja con contador.

## Correcciones de diseño de la revisión (aplicadas)
Estructura y LangGraph: planificador en código (no es un grafo) que elige por consulta qué seguimientos tocan y lanza un run del grafo 2 por cada uno con hilo estable (alerta + id + fecha), sin popups duplicados; estado del seguimiento con estado (pendiente, esperando decisión, pagada) y una "próxima fecha de alerta" (Cerrar = mañana, Posponer = fecha elegida); al iniciar se retoman las pausas pendientes y se reinician las extracciones interrumpidas; la decisión humana se valida dentro del grafo con re-pregunta (formulario único: acción, nueva fecha, ¿cheque?, N° de documento); la extracción es salida estructurada del modelo, sin herramientas ni ciclo de Haiku, y el código es quien guarda (el modelo no puede ordenar nada al sistema); reintento con retroalimentación (se le dicen al modelo los hallazgos del código), tope 1 o 2; "Aprobar tal cual" va directo a guardar (no revalida; marca aprobada manual) y "Corregir" vuelve a validar (evita un ciclo infinito cuando la factura trae un error propio); guardar es una sola transacción (factura, líneas y seguimiento); un hilo por factura y lote secuencial con avance visible (una falla no detiene las demás, las dudosas van a la bandeja Por revisar); fallas de red o API: transitorias con reintento y espera, permanentes con aviso claro (políticas de reintento de LangGraph); escrituras serializadas y la interfaz se actualiza solo desde su hilo; los checkpoints son desechables (dos archivos: checkpoints.db y facturas.db; lo crítico vive en facturas.db; la foto se copia a la carpeta de datos al registrar); la lógica de fechas recibe "hoy" como parámetro (reloj inyectable) para poder probarla.
Dominio chileno: el esquema agrega tipo de documento, monto exento y otros impuestos; validación: neto + exento + IVA + otros = total (±1 peso) más IVA cercano al 19% del neto; RUT con dígito verificador (módulo 11); fechas coherentes; solo las facturas generan alertas (notas de crédito y guías no); "dudosa" = cualquier hallazgo de las reglas de código (no se usa la confianza que declara el modelo); falta agrupar 2 fotos (se mantiene de la v1); vista de facturas para marcar pagada sin esperar la alerta.

## Estado de cada grafo (resumen para formalizar)
Grafo 1 (por factura): id de factura, rutas de las imágenes (nunca el contenido), campos extraídos (cabecera y líneas), hallazgos de validación y veredicto, número de intentos, decisión humana, resultado del guardado. Sin lista de mensajes; solo un registro de eventos acumularía datos. Grafo 2 (por vencimiento): id del seguimiento y de la factura, decisión recibida (acción, nueva fecha, con cheque, N° de documento), resultado del pago y de la regeneración del Excel.
Estados de la factura en la base: registrada, extrayendo, en revisión, guardada, rechazada, duplicada, error reintentable.

## Hallazgos de la v1 que la v2 corrige
La validación aritmética no se aplicaba (solo se informaba al modelo y el modelo se ponía el estado); no validaba RUT, fechas ni suma de líneas; save_file corría siempre aunque el guardado fallara; ciclo sin tope de iteraciones; el ciclo de Haiku reenviaba la imagen (el README dice lo contrario); una factura contaba como procesada aunque no se guardara; Excel por fecha de procesamiento; verify=False; en el CLI dos fotos son dos facturas (el README dice que es una); código muerto (save_to_excel); la GUI actualiza widgets desde un hilo secundario; sin detección de duplicados; sin impuestos adicionales ni facturas exentas en la validación.

## Pendientes
- Decidir si el histórico de la v1 (Excel de meses anteriores) debe importarse a la base de la v2 o queda aparte.
- Verificar la versión de Python instalada en el PC del cliente.
- Formalizar nodos, reglas de validación y estrategia de pruebas: pruebas unitarias con pytest para la validación y el planificador (con reloj inyectable); dataset de facturas anonimizadas con campos esperados y evaluadores por campo en LangSmith; comparar el modelo de visión actual con uno más barato usando el dataset. Después, los prompts para Claude Code.

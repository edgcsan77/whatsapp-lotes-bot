# WhatsApp Lotes Bot

Sistema para:

- Recibir solicitudes desde chats privados y grupos de WhatsApp.
- Detectar RFC y CURP.
- Priorizar RFC cuando un mensaje contenga RFC y CURP.
- Convertir CURP a RFC mediante consulta CURP Nuevo León y Moffin.
- Agrupar solicitudes en lotes.
- Enviar lotes a proveedores.
- Procesar respuestas de proveedores.
- Entregar resultados a los clientes.
- Generar cortes diarios automáticos.
- Administrar clientes, proveedores y solicitudes desde un panel web.

## Producción

- Ubuntu 24.04 LTS
- Python
- FastAPI
- PostgreSQL
- Redis
- Celery
- Evolution API
- Nginx
- Systemd

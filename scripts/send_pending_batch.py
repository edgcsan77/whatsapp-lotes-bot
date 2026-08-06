import argparse
import asyncio

from sqlalchemy import select

from app.database import SessionLocal
from app.models.client import Client
from app.models.provider import Provider
from app.services.batch_service import (
    BatchServiceError,
    create_pending_batch,
    send_existing_batch,
)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crea y envía un lote pendiente "
            "a un proveedor de WhatsApp."
        )
    )

    parser.add_argument(
        "--provider",
        required=True,
        help="Nombre exacto del proveedor",
    )

    parser.add_argument(
        "--client",
        default=None,
        help="Nombre exacto del cliente",
    )

    parser.add_argument(
        "--max-items",
        type=int,
        default=50,
    )

    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirma el envío real",
    )

    return parser.parse_args()


async def main() -> None:
    args = parse_arguments()

    with SessionLocal() as db:
        provider = db.scalar(
            select(Provider).where(
                Provider.name == args.provider
            )
        )

        if provider is None:
            raise SystemExit(
                "Proveedor no encontrado"
            )

        client_id = None

        if args.client:
            client = db.scalar(
                select(Client).where(
                    Client.name == args.client
                )
            )

            if client is None:
                raise SystemExit(
                    "Cliente no encontrado"
                )

            client_id = client.id

        try:
            creation = create_pending_batch(
                db,
                provider_id=provider.id,
                client_id=client_id,
                max_items=args.max_items,
            )

        except BatchServiceError as error:
            raise SystemExit(
                f"No se pudo crear el lote: {error}"
            ) from error

        print("\n=== LOTE CREADO ===")
        print("Batch ID:", creation.batch_id)
        print("Proveedor:", creation.provider_name)
        print("Destino:", creation.provider_jid)
        print("Solicitudes:", creation.request_ids)
        print("\n=== TEXTO ===")
        print(creation.outbound_text)

        if not args.confirm:
            print(
                "\nLote creado pero no enviado. "
                "Falta --confirm."
            )
            return

        try:
            result = await send_existing_batch(
                db,
                batch_id=creation.batch_id,
            )

        except BatchServiceError as error:
            raise SystemExit(
                f"No se pudo enviar el lote: {error}"
            ) from error

        print("\n=== ENVÍO COMPLETADO ===")
        print("Batch ID:", result.batch_id)
        print("Estado:", result.status)
        print(
            "Solicitudes:",
            result.request_count,
        )
        print(
            "Message ID:",
            result.provider_message_id,
        )


if __name__ == "__main__":
    asyncio.run(main())

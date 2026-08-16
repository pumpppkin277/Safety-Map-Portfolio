import argparse
import asyncio

from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.database import SessionLocal, init_db
from backend.app.models import Hotel
from backend.app.services.catalog import assess_hotel_environment, discover_city_hotels, refresh_environment_pois


async def run(args: argparse.Namespace) -> None:
    settings = get_settings()
    init_db()
    with SessionLocal() as session:
        discovery = await discover_city_hotels(
            session,
            city_name=args.city_name,
            city_code=args.city_code,
            settings=settings,
            max_pages=args.max_pages,
        )
        print("Discovery:", discovery)
        if args.environment_limit <= 0:
            return
        hotels = session.scalars(
            select(Hotel)
            .where(Hotel.city_code == args.city_code, Hotel.audit_status == "pending")
            .order_by(Hotel.id)
            .limit(args.environment_limit)
        ).all()
        succeeded = 0
        for hotel in hotels:
            try:
                await refresh_environment_pois(session, hotel, settings)
                await assess_hotel_environment(session, hotel, settings)
                succeeded += 1
                print("Assessed {}: {}".format(hotel.id, hotel.name))
            except Exception as exc:  # Each hotel is isolated so a batch can continue.
                session.rollback()
                print("Skipped {}: {}".format(hotel.id, exc))
        print("Environment assessments: {}/{}".format(succeeded, len(hotels)))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover a city and optionally run environment assessments.")
    parser.add_argument("--city-name", required=True)
    parser.add_argument("--city-code", required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--environment-limit", type=int, default=0)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))

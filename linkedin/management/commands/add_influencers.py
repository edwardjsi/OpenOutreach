import re
from django.core.management.base import BaseCommand
from linkedin.models import Influencer

class Command(BaseCommand):
    help = "Bulk add influencers from a text file (one URL per line)"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to text file containing LinkedIn URLs")

    def handle(self, *args, **options):
        file_path = options["file_path"]
        try:
            with open(file_path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            return

        added = 0
        skipped = 0

        for line in lines:
            url = line.strip()
            if not url:
                continue

            # Basic validation
            if "linkedin.com/in/" not in url:
                self.stdout.write(self.style.WARNING(f"Skipping invalid URL: {url}"))
                skipped += 1
                continue

            # Extract username for the model
            username = ""
            match = re.search(r'linkedin\.com/in/([^/]+)', url)
            if match:
                username = match.group(1)

            # Insert ignoring duplicates
            _, created = Influencer.objects.get_or_create(
                linkedin_url=url,
                defaults={"username": username}
            )

            if created:
                added += 1
                self.stdout.write(self.style.SUCCESS(f"Added: {url}"))
            else:
                skipped += 1
                self.stdout.write(f"Duplicate skipped: {url}")

        self.stdout.write(self.style.SUCCESS(f"Done. Added: {added}, Skipped/Duplicates: {skipped}"))

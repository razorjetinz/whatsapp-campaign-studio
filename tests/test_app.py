import base64
import tempfile
import unittest
from pathlib import Path

from app.app import create_app, normalize_phone, truthy


class CampaignStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "DATABASE_PATH": str(Path(self.tmp.name) / "test.db"),
            "APP_USERNAME": "tester",
            "APP_PASSWORD": "test-password-long-enough",
            "WEBHOOK_VERIFY_TOKEN": "test-webhook-secret",
            "DRY_RUN": True,
            "SEND_DELAY_SECONDS": 0.5,
            "META_ACCESS_TOKEN": "",
            "META_PHONE_NUMBER_ID": "",
            "META_WABA_ID": "",
            "META_APP_SECRET": "",
        })
        self.client = self.app.test_client()
        token = base64.b64encode(b"tester:test-password-long-enough").decode()
        self.auth = {"Authorization": f"Basic {token}"}
        self.write_headers = {**self.auth, "X-Requested-With": "WhatsAppCampaignStudio"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_phone_requires_explicit_international_format(self):
        self.assertEqual(normalize_phone("+91 98765-43210"), "+919876543210")
        with self.assertRaises(ValueError):
            normalize_phone("9876543210")

    def test_truthy_consent_values(self):
        self.assertTrue(truthy("yes"))
        self.assertTrue(truthy("TRUE"))
        self.assertFalse(truthy("no"))

    def test_dashboard_requires_basic_auth(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.get("/", headers=self.auth).status_code, 200)

    def test_mutations_require_app_header_and_consent(self):
        no_header = self.client.post("/api/groups", json={"name": "Customers"}, headers=self.auth)
        self.assertEqual(no_header.status_code, 403)
        no_consent = self.client.post("/api/contacts", json={
            "name": "Asha", "phone": "+919876543210", "opted_in": False,
            "consent_source": "Website form"
        }, headers=self.write_headers)
        self.assertEqual(no_consent.status_code, 400)

    def test_group_contact_and_campaign_draft(self):
        group = self.client.post("/api/groups", json={"name": "Customers"}, headers=self.write_headers)
        self.assertEqual(group.status_code, 201)
        group_id = group.get_json()["id"]
        contact = self.client.post("/api/contacts", json={
            "name": "Asha", "phone": "+919876543210", "opted_in": True,
            "consent_source": "Website signup"
        }, headers=self.write_headers)
        self.assertEqual(contact.status_code, 201)
        contact_id = contact.get_json()["id"]
        member = self.client.post(f"/api/groups/{group_id}/members", json={"contact_ids": [contact_id]}, headers=self.write_headers)
        self.assertEqual(member.status_code, 200)
        campaign = self.client.post("/api/campaigns", json={
            "name": "Welcome", "group_id": group_id, "template_name": "hello_world",
            "language": "en_US", "body_fields": []
        }, headers=self.write_headers)
        self.assertEqual(campaign.status_code, 201)
        self.assertEqual(campaign.get_json()["total"], 1)
        self.assertTrue(campaign.get_json()["dry_run"])


    def test_csv_opt_in_requires_consent_source(self):
        import io
        data = {"file": (io.BytesIO(b"name,phone,opted_in,consent_source\nAsha,+919876543210,yes,\n"), "contacts.csv")}
        response = self.client.post("/api/contacts/import", data=data, headers=self.write_headers, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_webhook_verification(self):
        response = self.client.get("/webhook?hub.mode=subscribe&hub.verify_token=test-webhook-secret&hub.challenge=12345")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "12345")


if __name__ == "__main__":
    unittest.main()

import unittest
import json
import sqlite3
from app import app
import database as db

class TestMultiImageProduct(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        # Initialize test database
        db.init_db()

        # Admin login session
        with self.client.session_transaction() as sess:
            sess["is_admin"] = True
            sess["admin_username"] = "admin"

    def test_multi_image_crud(self):
        # 1. Create a product with 3 photos
        fake_photo_1 = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP"
        fake_photo_2 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        fake_photo_3 = "https://example.com/saree-detail.jpg"

        payload = {
            "name": "Test Multi Photo Saree",
            "category": "bridal",
            "price": 24999,
            "description": "Exquisite silk saree with multiple angles.",
            "images": [fake_photo_1, fake_photo_2, fake_photo_3]
        }

        res = self.client.post("/api/admin/products", json=payload)
        self.assertEqual(res.status_code, 201)
        created = res.get_json()
        pid = created["id"]

        self.assertEqual(len(created["images"]), 3)
        self.assertEqual(created["imageData"], fake_photo_1)
        self.assertEqual(created["images"][0], fake_photo_1)
        self.assertEqual(created["images"][1], fake_photo_2)
        self.assertEqual(created["images"][2], fake_photo_3)

        # 2. Get product via public API
        get_res = self.client.get(f"/api/products/{pid}")
        self.assertEqual(get_res.status_code, 200)
        p = get_res.get_json()
        self.assertEqual(len(p["images"]), 3)
        self.assertEqual(p["imageData"], fake_photo_1)

        # 3. Update product photos (reorder or modify)
        update_payload = {
            "images": [fake_photo_2, fake_photo_1]
        }
        put_res = self.client.put(f"/api/admin/products/{pid}", json=update_payload)
        self.assertEqual(put_res.status_code, 200)
        updated = put_res.get_json()
        self.assertEqual(len(updated["images"]), 2)
        self.assertEqual(updated["imageData"], fake_photo_2)
        self.assertEqual(updated["images"][0], fake_photo_2)

        # 4. Backward compatibility test with single imageData
        legacy_payload = {
            "name": "Legacy Single Photo Saree",
            "category": "classic",
            "price": 18000,
            "description": "Legacy format saree.",
            "imageData": fake_photo_2
        }
        leg_res = self.client.post("/api/admin/products", json=legacy_payload)
        self.assertEqual(leg_res.status_code, 201)
        leg_created = leg_res.get_json()
        self.assertEqual(leg_created["imageData"], fake_photo_2)
        self.assertEqual(leg_created["images"], [fake_photo_2])

        # Clean up created test items
        self.client.delete(f"/api/admin/products/{pid}")
        self.client.delete(f"/api/admin/products/{leg_created['id']}")
        print("\nAll multi-photo tests passed successfully! ✅")

if __name__ == "__main__":
    unittest.main()

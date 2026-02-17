#!/usr/bin/env python3
"""
Interactive Demo: CBOR Iterative Construction

Demonstrates all the new builder pattern features with live examples.
"""

from simple_cbor import CBOR, cbor_encode, cbor_decode
import time

def separator(title=""):
    """Print separator"""
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)
    print()

def demo_basic_construction():
    """Demo 1: Basic iterative construction"""
    separator("Demo 1: Basic Iterative Construction")
    
    print("Building a user profile step by step...\n")
    
    # Start with empty dict
    user = CBOR({})
    print(f"Step 1 - Empty: {user.data}")
    
    # Add fields one by one
    user.set("id", "user-001")
    print(f"Step 2 - Add ID: {user.data}")
    
    user.set("name", "Alice")
    print(f"Step 3 - Add name: {user.data}")
    
    user.set("email", "alice@example.com")
    print(f"Step 4 - Add email: {user.data}")
    
    user.set("roles", [])
    print(f"Step 5 - Add empty roles: {user.data}")
    
    user["roles"].append("admin")
    user["roles"].append("developer")
    print(f"Step 6 - Add roles: {user.data}")
    
    # Encode
    cbor_bytes = user.encode()
    print(f"\nEncoded to {len(cbor_bytes)} bytes")
    print(f"Decoded: {cbor_decode(cbor_bytes)}")


def demo_fluent_api():
    """Demo 2: Fluent API / Method chaining"""
    separator("Demo 2: Fluent API (Method Chaining)")
    
    print("Building the same user with method chaining...\n")
    
    user = (CBOR({})
            .set("id", "user-001")
            .set("name", "Alice")
            .set("email", "alice@example.com")
            .set("roles", [])
            .update(active=True, verified=False))
    
    user["roles"].extend(["admin", "developer"])
    
    print(f"Final result: {user.data}")
    print(f"\nMuch more concise! ✨")


def demo_nested_access():
    """Demo 3: Nested path access"""
    separator("Demo 3: Nested Path Access")
    
    print("Building deeply nested structure with path notation...\n")
    
    config = CBOR({})
    
    # Set nested values (auto-creates intermediate dicts)
    config.set_nested("server.host", "localhost")
    config.set_nested("server.port", 8080)
    config.set_nested("server.ssl.enabled", True)
    config.set_nested("server.ssl.cert_path", "/path/to/cert.pem")
    config.set_nested("database.url", "postgres://localhost/mydb")
    config.set_nested("database.pool_size", 10)
    config.set_nested("logging.level", "INFO")
    config.set_nested("logging.output.file", "/var/log/app.log")
    
    print("Structure created:")
    import json
    print(json.dumps(config.data, indent=2))
    
    print("\nRetrieving nested values:")
    print(f"  Server host: {config.get_nested('server.host')}")
    print(f"  SSL enabled: {config.get_nested('server.ssl.enabled')}")
    print(f"  Pool size: {config.get_nested('database.pool_size')}")
    print(f"  Log file: {config.get_nested('logging.output.file')}")
    
    # With defaults
    print(f"  Missing value: {config.get_nested('server.timeout', default=30)}")


def demo_array_building():
    """Demo 4: Array building"""
    separator("Demo 4: Incremental Array Building")
    
    print("Building an array of sensor readings...\n")
    
    readings = CBOR([])
    
    # Simulate collecting readings
    for i in range(5):
        reading = {
            "timestamp": int(time.time() * 1000),
            "temperature": 20.0 + i * 0.5,
            "humidity": 45.0 + i * 2
        }
        readings.append(reading)
        print(f"Reading {i+1}: temp={reading['temperature']}°C, humidity={reading['humidity']}%")
    
    print(f"\nCollected {len(readings)} readings")
    print(f"Encoded size: {len(readings.encode())} bytes")


def demo_copy_and_merge():
    """Demo 5: Copy and merge operations"""
    separator("Demo 5: Copy and Merge")
    
    print("Creating template and variants...\n")
    
    # Create template
    template = CBOR({
        "type": "user",
        "active": True,
        "created_at": "2024-01-01"
    })
    
    print(f"Template: {template.data}")
    
    # Create variants
    admin = template.copy().set("role", "admin").set("permissions", ["all"])
    user = template.copy().set("role", "user").set("permissions", ["read"])
    guest = template.copy().set("role", "guest").set("active", False)
    
    print(f"\nAdmin: {admin.data}")
    print(f"User: {user.data}")
    print(f"Guest: {guest.data}")
    
    print("\nMerging two CBOR objects...\n")
    
    base_config = CBOR({"server": {"host": "localhost"}})
    extra_config = CBOR({"server": {"port": 8080}, "debug": True})
    
    print(f"Base: {base_config.data}")
    print(f"Extra: {extra_config.data}")
    
    # Note: merge only works at top level for dicts
    combined = base_config.copy()
    combined.update(extra_config.data)
    print(f"Combined: {combined.data}")


def demo_corim_building():
    """Demo 6: Real-world CoRIM structure"""
    separator("Demo 6: Building a CoRIM Structure")
    
    print("Building a complete CoRIM structure...\n")
    
    # Build CoRIM
    corim = (CBOR({})
             .set("id", "example.corim.001")
             .set("tags", []))
    
    print("Created base CoRIM with ID")
    
    # Add first tag
    tag1 = {
        "id": "tag-001",
        "environment": {
            "class": {"id": 1, "vendor": "ACME Corp"},
            "instance": b'\x01\x02\x03\x04'
        },
        "measurements": [
            {"type": "sha256", "value": b'\xaa' * 32}
        ]
    }
    
    corim["tags"].append(tag1)
    print(f"Added tag 1: {tag1['id']}")
    
    # Add second tag
    tag2 = {
        "id": "tag-002",
        "environment": {
            "class": {"id": 2, "vendor": "ACME Corp"},
            "instance": b'\x05\x06\x07\x08'
        },
        "measurements": [
            {"type": "sha384", "value": b'\xbb' * 48}
        ]
    }
    
    corim["tags"].append(tag2)
    print(f"Added tag 2: {tag2['id']}")
    
    # Wrap in tag 501 (unsigned-corim-map)
    tagged_corim = (501, corim.data)
    
    # Encode
    cbor_bytes = CBOR(tagged_corim).encode(canonical=True)
    
    print(f"\nFinal CoRIM:")
    print(f"  ID: {corim['id']}")
    print(f"  Tags: {len(corim['tags'])}")
    print(f"  Encoded size: {len(cbor_bytes)} bytes")
    print(f"  Canonical: Yes (deterministic)")


def demo_modify_existing():
    """Demo 7: Modifying existing CBOR"""
    separator("Demo 7: Modify and Re-encode")
    
    print("Creating initial data...\n")
    
    # Create initial data
    data = CBOR({
        "version": 1,
        "count": 0,
        "items": []
    })
    
    initial_bytes = data.encode()
    print(f"Initial: {data.data}")
    print(f"Encoded: {len(initial_bytes)} bytes")
    
    # Modify
    print("\nModifying...")
    data.set("version", 2)
    data.set("count", 3)
    data["items"].extend(["item1", "item2", "item3"])
    data.set("last_modified", "2024-01-15")
    
    # Re-encode
    modified_bytes = data.encode()
    print(f"Modified: {data.data}")
    print(f"Re-encoded: {len(modified_bytes)} bytes")
    
    # Verify both decode correctly
    print("\nVerifying...")
    print(f"  Initial decodes to: {cbor_decode(initial_bytes)}")
    print(f"  Modified decodes to: {cbor_decode(modified_bytes)}")


def demo_all_features():
    """Demo 8: All features together"""
    separator("Demo 8: All Features Together")
    
    print("Comprehensive example using all features...\n")
    
    # Build complex structure
    app = (CBOR({})
           .set("app_name", "MyApp")
           .set("version", "2.0.0")
           .set("config", {})
           .set("users", [])
           .set("metadata", {}))
    
    # Use nested access
    app.set_nested("config.server.host", "0.0.0.0")
    app.set_nested("config.server.port", 8080)
    app.set_nested("config.database.url", "postgres://localhost/myapp")
    app.set_nested("metadata.created", "2024-01-01")
    app.set_nested("metadata.author", "DevTeam")
    
    # Add users iteratively
    for name, role in [("Alice", "admin"), ("Bob", "user"), ("Charlie", "user")]:
        user = {
            "name": name,
            "role": role,
            "active": True
        }
        app["users"].append(user)
    
    # Update metadata
    app["metadata"].update({
        "user_count": len(app["users"]),
        "last_updated": "2024-01-15"
    })
    
    # Show result
    print("Final structure:")
    import json
    print(json.dumps(app.data, indent=2))
    
    # Encode
    cbor_bytes = app.encode(canonical=True)
    print(f"\nEncoded to {len(cbor_bytes)} bytes (canonical)")
    
    # Diagnostic dump
    print("\nCBOR Diagnostic Dump:")
    print(app.diag()[:500] + "..." if len(app.diag()) > 500 else app.diag())


def main():
    """Run all demos"""
    print("\n" + "=" * 70)
    print(" CBOR Iterative Construction - Interactive Demo")
    print("=" * 70)
    print("\nThis demo shows all the new builder pattern features!")
    print("Press Enter to continue through each demo...")
    input()
    
    demos = [
        demo_basic_construction,
        demo_fluent_api,
        demo_nested_access,
        demo_array_building,
        demo_copy_and_merge,
        demo_corim_building,
        demo_modify_existing,
        demo_all_features,
    ]
    
    for i, demo in enumerate(demos, 1):
        demo()
        
        if i < len(demos):
            print("\nPress Enter for next demo...")
            input()
    
    separator("All Demos Complete!")
    print("✨ You now know all the CBOR builder features! ✨")
    print("\nKey takeaways:")
    print("  1. Use .set(), .append(), .extend() for fluent building")
    print("  2. Use .set_nested() for deep structures")
    print("  3. Use .copy() to create variants")
    print("  4. Use .update() for bulk changes")
    print("  5. Direct access (.data, [key]) still works!")
    print("\nCheck the docs for more examples and patterns.")
    print("=" * 70)


if __name__ == '__main__':
    main()

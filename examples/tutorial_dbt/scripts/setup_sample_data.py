#!/usr/bin/env python3
"""Setup sample data for dbt example project."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import duckdb

# Create DuckDB database
db_path = "examples/tutorial_dbt/data/sample.duckdb"
conn = duckdb.connect(db_path)

# Generate sample data
regions = ["North", "South", "East", "West"]
products = [
    {"id": 1, "name": "Widget A", "category": "Widgets", "price": 10.0},
    {"id": 2, "name": "Widget B", "category": "Widgets", "price": 15.0},
    {"id": 3, "name": "Gadget X", "category": "Gadgets", "price": 25.0},
    {"id": 4, "name": "Gadget Y", "category": "Gadgets", "price": 30.0},
]

# Create customers
customers_data = []
for i in range(1, 101):
    customers_data.append(
        {
            "customer_id": i,
            "customer_name": f"Customer {i}",
            "region": random.choice(regions),
            "signup_date": (
                datetime.now() - timedelta(days=random.randint(0, 365))
            ).strftime("%Y-%m-%d"),
        }
    )

# Create products
products_data = list(products)

# Create orders
orders_data = []
start_date = datetime(2023, 1, 1)
for i in range(1, 1001):
    order_date = start_date + timedelta(days=random.randint(0, 365))
    product = random.choice(products)
    quantity = random.randint(1, 10)
    revenue = product["price"] * quantity

    orders_data.append(
        {
            "order_id": i,
            "order_date": order_date.strftime("%Y-%m-%d"),
            "customer_id": random.randint(1, 100),
            "product_id": product["id"],
            "quantity": quantity,
            "revenue": revenue,
            "region": random.choice(regions),
        }
    )

# Create schema and tables
conn.execute("CREATE SCHEMA IF NOT EXISTS main")
conn.execute("DROP TABLE IF EXISTS main.orders")
conn.execute("DROP TABLE IF EXISTS main.customers")
conn.execute("DROP TABLE IF EXISTS main.products")

# Create table structures
conn.execute(
    """
    CREATE TABLE main.customers (
        customer_id INTEGER,
        customer_name VARCHAR,
        region VARCHAR,
        signup_date DATE
    )
"""
)

conn.execute(
    """
    CREATE TABLE main.products (
        product_id INTEGER,
        product_name VARCHAR,
        category VARCHAR,
        price DOUBLE
    )
"""
)

conn.execute(
    """
    CREATE TABLE main.orders (
        order_id INTEGER,
        order_date DATE,
        customer_id INTEGER,
        product_id INTEGER,
        quantity INTEGER,
        revenue DOUBLE,
        region VARCHAR
    )
"""
)

# Insert customers
for customer in customers_data[:10]:  # Insert first 10 for demo
    conn.execute(
        f"""
        INSERT INTO main.customers VALUES
        ({customer["customer_id"]}, '{customer["customer_name"]}',
         '{customer["region"]}', '{customer["signup_date"]}')
    """
    )

# Insert products
for product in products_data:
    conn.execute(
        f"""
        INSERT INTO main.products VALUES
        ({product["id"]}, '{product["name"]}', '{product["category"]}', {product["price"]})
    """
    )

# Insert orders (sample)
for order in orders_data[:100]:  # Insert first 100 for demo
    conn.execute(
        f"""
        INSERT INTO main.orders VALUES
        ({order["order_id"]}, '{order["order_date"]}', {order["customer_id"]},
         {order["product_id"]}, {order["quantity"]}, {order["revenue"]}, '{order["region"]}')
    """
    )

conn.close()
print(f"✅ Sample data created in {db_path}")

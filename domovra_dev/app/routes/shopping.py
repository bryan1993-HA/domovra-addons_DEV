# app/routes/shopping.py
return RedirectResponse(f"{base}shopping?list_id={int(list_id)}", status_code=303)


@router.post("/shopping/list/delete")
async def delete_list(request: Request, list_id: int = Form(...)):
base = ingress_base(request)
with _conn() as c:
c.execute("DELETE FROM shopping_items WHERE list_id=?", (int(list_id),))
c.execute("DELETE FROM shopping_lists WHERE id=?", (int(list_id),))
return RedirectResponse(f"{base}shopping", status_code=303)


# ---------- Actions sur items ----------


@router.post("/shopping/item/add")
async def add_item(request: Request,
list_id: int = Form(...),
product_name: str = Form(...),
product_category: Optional[str] = Form(None),
qty_wanted: float = Form(1),
unit: Optional[str] = Form(None)):
base = ingress_base(request)
with _conn() as c:
c.execute(
"""
INSERT INTO shopping_items(list_id, product_name, product_category, qty_wanted, unit, is_checked)
VALUES(?,?,?,?,?,0)
""",
(int(list_id), _q(product_name), _q(product_category), float(qty_wanted or 1), _q(unit))
)
return RedirectResponse(f"{base}shopping?list_id={int(list_id)}", status_code=303)


@router.post("/shopping/item/toggle")
async def toggle_item(request: Request, id: int = Form(...), list_id: Optional[int] = Form(None)):
base = ingress_base(request)
with _conn() as c:
c.execute("UPDATE shopping_items SET is_checked = 1 - is_checked WHERE id=?", (int(id),))
q = f"{base}shopping"
if list_id: q += f"?list_id={int(list_id)}"
return RedirectResponse(q, status_code=303)


@router.post("/shopping/item/qty")
async def set_qty(request: Request, id: int = Form(...), qty_wanted: float = Form(...)):
base = ingress_base(request)
with _conn() as c:
c.execute("UPDATE shopping_items SET qty_wanted=? WHERE id=?", (float(qty_wanted), int(id)))
return RedirectResponse(f"{base}shopping", status_code=303)


@router.post("/shopping/item/edit")
async def edit_item(request: Request,
id: int = Form(...),
product_name: str = Form(...),
product_category: Optional[str] = Form(None),
qty_wanted: float = Form(1),
unit: Optional[str] = Form(None),
note: Optional[str] = Form(None)):
base = ingress_base(request)
with _conn() as c:
c.execute(
"UPDATE shopping_items SET product_name=?, product_category=?, qty_wanted=?, unit=?, note=? WHERE id=?",
(_q(product_name), _q(product_category), float(qty_wanted or 1), _q(unit), _q(note), int(id))
)
return RedirectResponse(f"{base}shopping", status_code=303)


@router.post("/shopping/item/delete")
async def delete_item(request: Request, id: int = Form(...)):
base = ingress_base(request)
with _conn() as c:
c.execute("DELETE FROM shopping_items WHERE id=?", (int(id),))
return RedirectResponse(f"{base}shopping", status_code=303)


@router.post("/shopping/check_all")
async def check_all(request: Request, list_id: int = Form(...)):
base = ingress_base(request)
with _conn() as c:
c.execute("UPDATE shopping_items SET is_checked=1 WHERE list_id=?", (int(list_id),))
return RedirectResponse(f"{base}shopping?list_id={int(list_id)}", status_code=303)


@router.post("/shopping/uncheck_all")
async def uncheck_all(request: Request, list_id: int = Form(...)):
base = ingress_base(request)
with _conn() as c:
c.execute("UPDATE shopping_items SET is_checked=0 WHERE list_id=?", (int(list_id),))
return RedirectResponse(f"{base}shopping?list_id={int(list_id)}", status_code=303)
# properties.py
# Base de datos de propiedades inmobiliarias

PROPERTIES = [
    {
        "id": 1,
        "nombre": "Costa Azul",
        "descripcion": "Condominios frente al mar con acceso directo a la playa. Perfectos para inversión o casa de descanso.",
        "ubicacion": "Puerto Vallarta, Jalisco",
        "precio": 5200000,
        "cuartos": 2,
        "banos": 2,
        "area": 140,
        "keywords": ["playa", "costa", "mar", "vallarta", "frente al mar", "condominio", "jalisco"]
    },
    {
        "id": 2,
        "nombre": "Residencial Los Pinos",
        "descripcion": "Casas en privada con amenidades familiares. Parque infantil, cancha deportiva y casa club. Ambiente tranquilo y seguro.",
        "ubicacion": "Veracruz, Veracruz",
        "precio": 2800000,
        "cuartos": 3,
        "banos": 2,
        "area": 150,
        "keywords": ["veracruz", "familia", "familiar", "niños", "infantil", "deportiva", "casa club", "privada", "seguro", "tranquilo"]
    },
    {
        "id": 3,
        "nombre": "Residencial Vista Hermosa",
        "descripcion": "Exclusivo desarrollo residencial con acabados de lujo, áreas verdes y seguridad las 24 horas. Diseño moderno con espacios amplios y luminosos.",
        "ubicacion": "Tijuana, Baja California",
        "precio": 3500000,
        "cuartos": 3,
        "banos": 2,
        "area": 180,
        "keywords": ["tijuana", "baja california", "lujo", "residencial", "seguridad", "moderno", "áreas verdes", "exclusivo"]
    },
    {
        "id": 4,
        "nombre": "Villas del Mar",
        "descripcion": "Desarrollo costero con vista al océano. Acceso privado a la playa, club de playa exclusivo y arquitectura contemporánea.",
        "ubicacion": "Cancún, Quintana Roo",
        "precio": 8500000,
        "cuartos": 4,
        "banos": 3,
        "area": 250,
        "keywords": ["cancún", "quintana roo", "playa", "costa", "océano", "mar", "club de playa", "vista al mar", "contemporáneo", "lujo"]
    },
    {
        "id": 5,
        "nombre": "Sky Residences",
        "descripcion": "Lujo en altura con vistas panorámicas de la ciudad. Penthouse disponibles con terrazas amplias y acabados premium.",
        "ubicacion": "Monterrey, Nuevo León",
        "precio": 6500000,
        "cuartos": 3,
        "banos": 3,
        "area": 200,
        "keywords": ["monterrey", "nuevo león", "penthouse", "lujo", "vistas", "panorámicas", "terraza", "premium", "ciudad", "altura"]
    }
]

def format_price(price):
    """Formatea el precio en formato legible"""
    return f"${price:,.0f} MXN"

def get_property_by_id(property_id):
    """Obtiene una propiedad por ID"""
    for prop in PROPERTIES:
        if prop["id"] == property_id:
            return prop
    return None

def get_property_description(property_id):
    """Genera una descripción completa de la propiedad"""
    prop = get_property_by_id(property_id)
    if not prop:
        return None
    
    return f"""
Propiedad: {prop['nombre']}
Ubicación: {prop['ubicacion']}
Descripción: {prop['descripcion']}
Precio: {format_price(prop['precio'])}
Características: {prop['cuartos']} recámaras, {prop['banos']} baños, {prop['area']} m²
""".strip()

def search_properties(query):
    """Busca propiedades basadas en palabras clave"""
    query_lower = query.lower()
    matching = []
    
    for prop in PROPERTIES:
        # Buscar en keywords
        for keyword in prop["keywords"]:
            if keyword in query_lower:
                matching.append(prop)
                break
        
        # Buscar en ubicación
        if prop["ubicacion"].lower() in query_lower:
            if prop not in matching:
                matching.append(prop)
    
    return matching

def get_all_properties_summary():
    """Obtiene un resumen de todas las propiedades disponibles"""
    if not PROPERTIES:
        return "No tenemos propiedades disponibles en este momento."
    
    summaries = []
    for prop in PROPERTIES:
        summaries.append(
            f"{prop['nombre']} en {prop['ubicacion']} - "
            f"{prop['cuartos']} recámaras, {format_price(prop['precio'])}"
        )
    
    return "Propiedades disponibles:\n" + "\n".join(summaries)

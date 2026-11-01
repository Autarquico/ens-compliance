"""Variantes de formato que no deben llegar a disco, con el texto que las rodea."""

# Cada valor contiene un dato personal o una credencial; ninguno es un id legítimo.
REJECTED = {
    "credencial con espacio antes de ':'": "password : S3cretValue!",
    "api_key con espacio antes de ':'": "api_key : Abcd1234XYZ",
    "credencial con espacios en '='": "token  =  Abcd1234XYZ",
    "credencial entrecomillada con espacios": 'password: "abc def 123"',
    "credencial entrecomillada simple": "clave = 'abc def 123'",
    "teléfono con paréntesis": "tel: (612) 345 678",
    "teléfono internacional con paréntesis": "+44 (20) 7946 0958",
    "teléfono con doble ':'": "tel::612345678",
    "teléfono con punto": "tel.612345678",
    "teléfono con guiones": "612-345-678",
    "teléfono con barras": "612/345/678",
    "identificador numérico largo": "9876543210",
    "IBAN en minúsculas": "es91 2100 0418 4502 0005 1332",
    "IBAN con guiones": "ES91-2100-0418-4502-0005-1332",
    "IBAN mixto sin espacios": "Es9121000418450200051332",
    "DNI en minúsculas": "12345678z",
    "DNI con separadores": "12.345.678-Z",
    "NIE con espacios": "x 1234567 l",
    "correo en mayúsculas": "CONTACTO.FICTICIO@EXAMPLE.ORG",
    "contraseña solo con letras tras ': '": "password : abcdef",
    "frase de contraseña entrecomillada": 'password : "correct horse battery staple"',
    "secret solo con letras": "secret: abcdef",
    "teléfono con comas": "tel: 612,345,678",
}

# Textos que se siguen admitiendo (sin error).
ACCEPTED = [
    "INC-2026-001",
    "Ver SIM-2026-012 y EV-20261015-0123abcd",
    "ticket 1234",
    "Fecha 2026-10-15",
    "Instante 2026-10-15T12:30:45+02:00",
    "Instante 2026-10-15 12:30:45",
    "Hora 12:30:45.123456",
    "clave: revisar",
    "la clave del problema fue el reloj",
    "token: abc",
    'clave: "revisar el tema"',
    "Fecha 2026-10-15 y 20261015 a las",
    "Pasos 1, 2, 3, 4, 5, 6, 7, 8 y 9",
    "secret scanning activado en todos los repos",
]

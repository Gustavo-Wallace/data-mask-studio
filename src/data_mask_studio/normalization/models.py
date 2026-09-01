from enum import StrEnum


class NormalizationRule(StrEnum):
    EXACT = "exact"
    DIGITS_ONLY = "digits_only"
    CPF = "cpf"
    CNPJ = "cnpj"
    PHONE = "phone"
    EMAIL = "email"
    IP_ADDRESS = "ip_address"
    COLLAPSE_WHITESPACE = "collapse_whitespace"
    PERSON_NAME = "person_name"

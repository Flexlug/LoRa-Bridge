"""LoRa-Bridge: двунаправленный мост LoRa-сети (MeshCore, …) ↔ мессенджеры.

Поддерживает несколько физических LoRa-нод (секция ``lora`` — массив), каждая со
своим ``type`` (meshcore | meshtastic — фундамент под LoRa↔LoRa-мостинг).

Архитектура и обоснование решений — docs/ARCHITECTURE.md.
Слои (зависимости направлены внутрь): transports/config → domain; core → domain.
"""

__version__ = "0.0.1"

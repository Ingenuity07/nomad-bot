class ProspectingError(Exception):
    """Base exception for all Nomad Prospecting Engine errors."""
    pass


class DiscoveryError(ProspectingError):
    """Exception raised when an external business discovery provider fails."""
    pass


class NormalizationError(ProspectingError):
    """Exception raised when input data cannot be normalized into target format."""
    pass


class ResolutionError(ProspectingError):
    """Exception raised when entity resolution or deduplication fails."""
    pass

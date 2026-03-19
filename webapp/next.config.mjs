
const nextConfig = {
  turbopack: {},
  webpack: (config, { isServer }) => {
    // Needed for pdf-parse (server-side)
    config.resolve.alias.canvas = false
    config.resolve.alias.encoding = false

    // Éviter que pdfjs-dist soit bundlé côté serveur
    if (isServer) {
      const existing = config.externals || []
      config.externals = Array.isArray(existing)
        ? [...existing, 'pdfjs-dist', 'react-pdf']
        : existing
    }

    return config
  },
}

export default nextConfig

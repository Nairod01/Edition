

const nextConfig = {
  webpack: (config) => {
    // Needed for pdf-parse
    config.resolve.alias.canvas = false
    config.resolve.alias.encoding = false
    return config
  },
}

export default nextConfig

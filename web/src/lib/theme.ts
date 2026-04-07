export const pwc = {
  // Primary
  black: '#000000',
  white: '#FFFFFF',
  orange500: '#FD5108',   // Primary accent — buttons, active states, links
  orange700: '#C52B09',   // Hover/pressed state
  orange400: '#FE7C39',   // Light accent — progress bars, active indicators
  orange100: '#FFE8D4',   // Tint — backgrounds for highlighted content
  orange50:  '#FFF5ED',   // Subtle tint — hover backgrounds, empty field highlight

  // Greys
  grey50:  '#F5F7F8',     // Page background
  grey100: '#EEEFF1',     // Card backgrounds, alternating rows
  grey200: '#DFE3E6',     // Borders, dividers
  grey300: '#CBD1D6',     // Disabled text, pending step connectors
  grey500: '#A1A8B3',     // Secondary text, timestamps
  grey700: '#787E8A',     // Tertiary text
  grey800: '#4C5056',     // Body text
  grey900: '#303236',     // Headings, primary text

  // Semantic
  success: '#16A34A',     // Green — completed steps, success badges
  error:   '#DC2626',     // Red — errors, failed states
  thinking: '#7C3AED',    // Purple — agent thinking/reasoning blocks

  // Typography
  fontHeading: '"Arial", "Helvetica Neue", sans-serif',
  fontBody:    '"Arial", sans-serif',
  fontMono:    '"SF Mono", "Fira Code", "Consolas", monospace',

  // Spacing scale (px)
  space: { xs: 4, sm: 8, md: 12, lg: 16, xl: 24, xxl: 32 },

  // Border radius
  radius: { sm: 4, md: 8, lg: 12 },

  // Shadows
  shadow: {
    card: '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06)',
    elevated: '0 4px 12px rgba(0,0,0,0.1)',
    modal: '0 20px 60px rgba(0,0,0,0.2)',
  },
} as const;

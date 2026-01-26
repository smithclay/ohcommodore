# React Hello World with Component Library Specification

## Objective

Create a production-ready React application with TypeScript, a reusable component library documented in Storybook, and comprehensive documentation. The app should demonstrate best practices for React development including testing, linting, and type safety.

## Architecture

- **Build tool**: Vite with React + TypeScript template
- **Testing**: Vitest with React Testing Library
- **Documentation**: Storybook for component documentation
- **Linting**: ESLint with React plugins, Prettier for formatting
- **Type safety**: TypeScript in strict mode

## Components

### Button
- Variants: primary, secondary, outline
- Sizes: sm, md, lg
- Full TypeScript types for props

### Card
- Slots: header, body, footer
- Variants: elevated, outlined
- Composable structure

### Input
- Controlled input with label
- Error state and helper text
- Accessibility: proper label association

## Error Handling

- Components should gracefully handle missing optional props
- Input component should display error states clearly
- All components should have sensible defaults

## Testing Strategy

- Unit tests for each component using Vitest + React Testing Library
- Test all variants and states render correctly
- Test user interactions (clicks, input changes)
- Test accessibility (proper roles, label associations)
- Integration test for main App

## Exit Criteria

The following commands must pass:
- `npm run lint` - ESLint passes
- `npm run typecheck` - TypeScript compiles without errors
- `npm test -- --run` - All tests pass
- `npm run build` - Production build succeeds

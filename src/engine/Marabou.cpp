/*! \file Marabou.cpp
 ** \verbatim
 ** Top contributors (to current version):
 **   Guy Katz
 ** This file is part of the Marabou project.
 ** Copyright (c) 2016-2017 by the authors listed in the file AUTHORS
 ** in the top-level source directory) and their institutional affiliations.
 ** All rights reserved. See the file COPYING in the top-level source
 ** directory for licensing information.\endverbatim
 **/

#include "AcasParser.h"
#include "File.h"
#include "MStringf.h"
#include "Marabou.h"
#include "Options.h"
#include "PropertyParser.h"
#include "ReluplexError.h"

Marabou::Marabou()
    : _acasParser( NULL )
{
}

Marabou::~Marabou()
{
    if ( _acasParser )
    {
        delete _acasParser;
        _acasParser = NULL;
    }
}

void Marabou::run( int argc, char **argv )
{
    struct timespec start = TimeUtils::sampleMicro();

    Options *options = Options::get();
    options->parseOptions( argc, argv );

    prepareInputQuery();
    solveQuery();

    struct timespec end = TimeUtils::sampleMicro();

    unsigned long long totalElapsed = TimeUtils::timePassed( start, end );
    displayResults( totalElapsed );
}

void Marabou::prepareInputQuery()
{
    /*
      Step 1: extract the network
    */
    String networkFilePath = Options::get()->getString( Options::INPUT_FILE_PATH );
    if ( !File::exists( networkFilePath ) )
    {
        printf( "Error: the specified network file (%s) doesn't exist!\n", networkFilePath.ascii() );
        throw ReluplexError( ReluplexError::FILE_DOESNT_EXIST, networkFilePath.ascii() );
    }

    // For now, assume the network is given in ACAS format
    _acasParser = new AcasParser( networkFilePath );
    _acasParser->generateQuery( _inputQuery );

    /*
      Step 2: extract the property in question
    */
    String propertyFilePath = Options::get()->getString( Options::PROPERTY_FILE_PATH );
    if ( propertyFilePath != "" )
        PropertyParser().parse( propertyFilePath, _inputQuery );
}

void Marabou::solveQuery()
{
    if ( _engine.processInputQuery( _inputQuery ) )
        _engine.solve();

    if ( _engine.getExitCode() == Engine::SAT )
        _engine.extractSolution( _inputQuery );
}

void Marabou::displayResults( unsigned long long microSecondsElapsed ) const
{
    Engine::ExitCode result = _engine.getExitCode();
    String resultString;

    if ( result == Engine::UNSAT )
    {
        resultString = "UNSAT";
        printf( "UNSAT\n" );
    }
    else if ( result == Engine::SAT )
    {
        resultString = "SAT";
        printf( "SAT\n\n" );

        printf( "Input assignment:\n" );
        for ( unsigned i = 0; i < _inputQuery.getNumInputVariables(); ++i )
            printf( "\tx%u = %8.4lf\n", i, _inputQuery.getSolutionValue( _inputQuery.inputVariableByIndex( i ) ) );

        printf( "\n" );
        printf( "Output:\n" );
        for ( unsigned i = 0; i < _inputQuery.getNumOutputVariables(); ++i )
            printf( "\ty%u = %8.4lf\n", i, _inputQuery.getSolutionValue( _inputQuery.outputVariableByIndex( i ) ) );
        printf( "\n" );
    }
    else if ( result == Engine::TIMEOUT )
    {
        resultString = "TIMEOUT";
        printf( "Timeout\n" );
    }
    else if ( result == Engine::ERROR )
    {
        resultString = "ERROR";
        printf( "Error\n" );
    }
    else
    {
        resultString = "UNKNOWN";
        printf( "UNKNOWN EXIT CODE! (this should not happen)" );
    }

    // Create a summary file, if requested
    String summaryFilePath = Options::get()->getString( Options::SUMMARY_FILE );
    if ( summaryFilePath != "" )
    {
        File summaryFile( summaryFilePath );
        summaryFile.open( File::MODE_WRITE_TRUNCATE );

        // Field #1: result
        summaryFile.write( resultString );

        // Field #2: total elapsed time
        summaryFile.write( Stringf( " %u ", microSecondsElapsed / 1000000 ) ); // In seconds

        // Field #3: number of visited tree states
        summaryFile.write( Stringf( "%u ",
                                    _engine.getStatistics()->getNumVisitedTreeStates() ) );

        // Field #4: average pivot time in micro seconds
        summaryFile.write( Stringf( "%u",
                                    _engine.getStatistics()->getAveragePivotTimeInMicro() ) );

        summaryFile.write( "\n" );
    }
}

//
// Local Variables:
// compile-command: "make -C ../.. "
// tags-file-name: "../../TAGS"
// c-basic-offset: 4
// End:
//